"""Send Message Tool -- cross-channel messaging via platform APIs.

    Sends a message to a user or channel on any connected messaging platform.
    Supports listing available targets and resolving human-friendly channel
    names to IDs. Works in both CLI and gateway contexts.
"""

import asyncio
import json
import logging
import os
import re
import time

from agent.redact import redact_sensitive_text
from agent.secret_scope import get_secret

logger = logging.getLogger(__name__)

# Email addresses — a valid email like "user@domain.com" should be treated as
# an explicit target for the email platform, not fall through to channel-name
# resolution which has no way to resolve a raw address.
_EMAIL_TARGET_RE = re.compile(r"^\s*[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\s*$")
# Most platforms read their home channel from "<PLATFORM>_HOME_CHANNEL", but a
# few diverge. Email reads EMAIL_HOME_ADDRESS (see gateway/config.py), so the
# generic "<PLATFORM>_HOME_CHANNEL" hint would point users at a variable that is
# never read. Map the exceptions so the error guidance is actually actionable.
_HOME_CHANNEL_ENV_OVERRIDES = {"email": "EMAIL_HOME_ADDRESS"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp"}
_AUDIO_EXTS = {".ogg", ".opus", ".mp3", ".m2a", ".wav", ".m4a", ".flac"}
_VOICE_EXTS = {".ogg", ".opus"}
_TELEGRAM_SEND_AUDIO_EXTS = {".mp3", ".m4a"}

# Extensions that carry a native caption on the media bubble itself
# (photo/video/document). Voice/audio notes are excluded: a caption on a
# voice note reads as a separate label rather than a bubble caption, and the
# established convention is to keep the accompanying text as its own message.
_CAPTIONABLE_EXTS = _IMAGE_EXTS | _VIDEO_EXTS | {
    ".pdf", ".doc", ".docx", ".txt", ".md", ".csv", ".xlsx", ".zip",
}

# more generous, so a conservative shared ceiling keeps behavior predictable.

def prepare_send_message_platforms() -> None:
    """Load enabled standalone plugins before tool schemas/cache keys are built."""
    from hermes_cli.plugins import discover_plugins

    discover_plugins()

def _media_caption_split(text, media_files, *, max_caption_len):
    """Decide whether the accompanying text should ride on the media bubble.

    Single enforced chokepoint for the ``MEDIA:<path> caption`` behavior
    across every standalone sender. ``hermes send`` (and the send_message
    tool / cron) strips the ``MEDIA:`` tag and leaves the remaining prose as
    ``text``; historically each platform sent that ``text`` as a *separate*
    message before an uncaptioned media bubble, splitting the reported case
    ``hermes send --to email "MEDIA:/x.png This Caption"`` into two parts.

    Returns ``(caption, body_text)``:

    * ``(caption, "")`` — attach ``text`` to the media as its native caption
      and send *no* separate body message. Only when there is exactly one
      media file, it is a captionable kind (image/video/document, not a
      voice/audio note), and ``text`` fits ``max_caption_len``.
    * ``(None, text)`` — keep the historical behavior: ``text`` is a separate
      body message and the media carries no caption. Applies to multi-file
      sends (caption→file association is ambiguous), voice/audio notes, empty
      text, or text longer than the caption limit.
    """
    stripped = (text or "").strip()
    media = media_files or []
    if not stripped or len(media) != 1:
        return None, text
    media_path, is_voice = media[0]
    if is_voice:
        return None, text
    ext = os.path.splitext(media_path)[1].lower()
    if ext not in _CAPTIONABLE_EXTS:
        return None, text
    # Measure the caption in Unicode codepoints — a portable upper bound that
    # never under-counts vs Telegram's UTF-16 units for BMP text, so an
    # over-count only fails safe (falls back to a separate message). The
    # Telegram call site additionally re-checks the *formatted* caption in
    # UTF-16 units, since MarkdownV2/HTML escaping can inflate the length.
    if len(stripped) > max_caption_len:
        return None, text
    return stripped, ""
_URL_SECRET_QUERY_RE = re.compile(
    r"([?&](?:access_token|api[_-]?key|auth[_-]?token|token|signature|sig)=)([^&#\s]+)",
    re.IGNORECASE,
)
_GENERIC_SECRET_ASSIGN_RE = re.compile(
    r"\b(access_token|api[_-]?key|auth[_-]?token|signature|sig)\s*=\s*([^\s,;]+)",
    re.IGNORECASE,
)

def _sanitize_error_text(text) -> str:
    """Redact secrets from error text before surfacing it to users/models."""
    redacted = redact_sensitive_text(text)
    redacted = _URL_SECRET_QUERY_RE.sub(lambda m: f"{m.group(1)}***", redacted)
    redacted = _GENERIC_SECRET_ASSIGN_RE.sub(lambda m: f"{m.group(1)}=***", redacted)
    return redacted

def _error(message: str) -> dict:
    """Build a standardized error payload with redacted content."""
    return {"error": _sanitize_error_text(message)}

def _display_chat_id(platform_name: str, chat_id: str) -> str:
    """Return a result-safe chat identifier for tool transcripts/log consumers."""
    return chat_id

def send_message_tool(args, **kw):
    """Handle cross-channel send_message tool calls."""
    action = args.get("action", "send")

    if action == "list":
        return _handle_list()

    if action == "react":
        return _handle_react(args)

    if action == "unreact":
        return _handle_react(args, remove=True)

    return _handle_send(args)

def _handle_list():
    """Return formatted list of available messaging targets."""
    try:
        from gateway.channel_directory import format_directory_for_display
        return json.dumps({"targets": format_directory_for_display()})
    except Exception as e:
        return json.dumps(_error(f"Failed to load channel directory: {e}"))

def _handle_react(args, remove=False):
    """Attach (or with ``remove=True`` retract) an emoji reaction on a message
    via a live gateway adapter.

    Only adapters that expose ``add_reaction(chat_id, emoji, message_id)`` /
    ``remove_reaction(chat_id, message_id)`` coroutines support this (e.g.
    photon/iMessage tapbacks). Requires the gateway to be running in this
    process — there is no standalone fallback, since reacting needs the
    adapter's live message-id state.
    """
    target = args.get("target", "")
    emoji = (args.get("emoji") or "").strip()
    message_id = (args.get("message_id") or "").strip() or None
    if not target or (not remove and not emoji):
        return tool_error(
            "Both 'target' and 'emoji' are required when action='react'"
            if not remove
            else "'target' is required when action='unreact'"
        )

    parts = target.split(":", 1)
    platform_name = parts[0].strip().lower()
    target_ref = parts[1].strip() if len(parts) > 1 else None
    chat_id = None
    prepare_send_message_platforms()
    if target_ref:
        chat_id, _thread_id, resolution_error = resolve_send_target(
            platform_name, target_ref
        )
        if resolution_error:
            return tool_error(resolution_error)

    try:
        from gateway.config import Platform, load_gateway_config
        platform = Platform(platform_name)
    except (ValueError, KeyError):
        return tool_error(f"Unknown platform: {platform_name}")

    if not chat_id:
        try:
            config = load_gateway_config()
            home = config.get_home_channel(platform)
        except Exception:
            home = None
        if not home:
            return tool_error(
                f"No chat specified and no home channel set for {platform_name}. "
                f"Use '{platform_name}:chat_id'."
            )
        chat_id = home.chat_id

    runner = None
    try:
        from gateway.run import _gateway_runner_ref
        runner = _gateway_runner_ref()
    except Exception:
        runner = None
    adapter = runner.adapters.get(platform) if runner is not None else None
    if adapter is None:
        return tool_error(
            f"Reactions require a live {platform_name} adapter in the running "
            "gateway (not available from cron/standalone contexts)."
        )
    fn_name = "remove_reaction" if remove else "add_reaction"
    react_fn = getattr(adapter, fn_name, None)
    if not callable(react_fn):
        return tool_error(
            f"Platform '{platform_name}' does not support message reactions."
        )

    try:
        from model_tools import _run_async
        if remove:
            result = _run_async(
                react_fn(chat_id=chat_id, message_id=message_id)
            )
        else:
            result = _run_async(
                react_fn(chat_id=chat_id, emoji=emoji, message_id=message_id)
            )
    except Exception as e:
        return json.dumps(_error(f"Reaction failed: {e}"))
    if isinstance(result, dict):
        return json.dumps(result)
    return json.dumps({"success": bool(result)})

def _handle_send(args):
    """Send a message to a platform target."""
    target = args.get("target", "")
    message = args.get("message", "")
    if not target or not message:
        return tool_error("Both 'target' and 'message' are required when action='send'")

    parts = target.split(":", 1)
    platform_name = parts[0].strip().lower()
    target_ref = parts[1].strip() if len(parts) > 1 else None
    chat_id = None
    thread_id = None

    prepare_send_message_platforms()
    if target_ref:
        chat_id, thread_id, resolution_error = resolve_send_target(
            platform_name, target_ref
        )
        if resolution_error:
            return tool_error(resolution_error)

    from tools.interrupt import is_interrupted
    if is_interrupted():
        return tool_error("Interrupted")

    try:
        from gateway.config import load_gateway_config, Platform
        config = load_gateway_config()
    except Exception as e:
        return json.dumps(_error(f"Failed to load gateway config: {e}"))

    from gateway.platform_registry import platform_registry

    entry = platform_registry.get(platform_name)
    is_builtin = platform_name in {member.value for member in Platform}
    if not is_builtin and entry is None:
        return tool_error(
            f"Unknown or unregistered plugin platform: {platform_name}"
        )
    try:
        platform = Platform(platform_name)
    except (ValueError, KeyError):
        return tool_error(f"Unknown platform: {platform_name}")

    pconfig = config.platforms.get(platform)
    if not pconfig or not pconfig.enabled:
        return tool_error(
            f"Platform '{platform_name}' is not configured. See the config docs."
        )

    from gateway.platforms.base import BasePlatformAdapter

    # Capture [[as_document]] directive before extract_media strips it.
    # Image-extension files in this batch will route through send_document
    # instead of send_photo so the original bytes survive (e.g. info-graph
    # JPGs where Telegram's sendPhoto recompresses to 1280px).
    force_document_attachments = "[[as_document]]" in message

    media_files, cleaned_message = BasePlatformAdapter.extract_media(message)
    media_files = BasePlatformAdapter.filter_media_delivery_paths(media_files)
    mirror_text = cleaned_message.strip() or _describe_media_for_mirror(media_files)

    used_home_channel = False
    if not chat_id:
        home = config.get_home_channel(platform)
        if home:
            chat_id = home.chat_id
            used_home_channel = True
        else:
            home_env = _HOME_CHANNEL_ENV_OVERRIDES.get(
                platform_name, f"{platform_name.upper()}_HOME_CHANNEL"
            )
            return tool_error(
                f"No home channel set for {platform_name} to determine where to send the message. "
                f"Either specify a channel directly with '{platform_name}:CHANNEL_NAME', "
                f"or set a home channel via: hermes config set {home_env} <channel_id>"
            )

    duplicate_skip = _maybe_skip_cron_duplicate_send(platform_name, chat_id, thread_id)
    if duplicate_skip:
        return json.dumps(duplicate_skip)

    try:
        from model_tools import _run_async
        send_kwargs = {
            "thread_id": thread_id,
            "media_files": media_files,
            "force_document": force_document_attachments,
        }
        # Preserve the exact built-in call contract; only custom handlers need
        # the complete typed request.
        if entry is not None and entry.send_message_handler is not None:
            send_kwargs["args"] = args
        result = _run_async(
            _send_to_platform(
                platform,
                pconfig,
                chat_id,
                cleaned_message,
                **send_kwargs,
            )
        )
        if used_home_channel and isinstance(result, dict) and result.get("success"):
            result["note"] = f"Sent to {platform_name} home channel (chat_id: {chat_id})"

        # Mirror the sent message into the target's gateway session
        if isinstance(result, dict) and result.get("success") and mirror_text:
            try:
                from gateway.mirror import mirror_to_session
                from gateway.session_context import get_session_env
                source_label = get_session_env("HERMES_SESSION_PLATFORM", "cli")
                user_id = get_session_env("HERMES_SESSION_USER_ID", "") or None
                if mirror_to_session(
                    platform_name,
                    chat_id,
                    mirror_text,
                    source_label=source_label,
                    thread_id=thread_id,
                    user_id=user_id,
                ):
                    result["mirrored"] = True
            except Exception:
                pass

        if isinstance(result, dict) and "error" in result:
            result["error"] = _sanitize_error_text(result["error"])
        return json.dumps(result)
    except Exception as e:
        return json.dumps(_error(f"Send failed: {e}"))

def _parse_target_ref(platform_name: str, target_ref: str):
    """Parse a tool target into chat_id/thread_id and whether it is explicit."""
    if platform_name == "email":
        match = _EMAIL_TARGET_RE.fullmatch(target_ref)
        if match:
            return target_ref.strip(), None, True
    stripped_target = target_ref.strip()
    if target_ref.lstrip("-").isdigit():
        return target_ref, None, True

    return None, None, False

def resolve_send_target(
    platform_name: str, target_ref: str
) -> tuple[str | None, str | None, str | None]:
    """Resolve one send target identically for model/CLI/cron surfaces.

    Channel-directory IDs are trusted. Plugin platforms must explicitly parse
    native target syntax; unresolved strings never receive an opaque fallback.
    The optional validator is the final authority over parser-normalized and
    directory-resolved IDs.
    """
    from gateway.config import Platform
    from gateway.platform_registry import platform_registry

    entry = platform_registry.get(platform_name)

    def _validate(candidate: str) -> str | None:
        if entry is None or entry.validate_target_ref_fn is None:
            return None
        try:
            verdict = entry.validate_target_ref_fn(candidate)
        except Exception:
            logger.debug(
                "Plugin target validator failed for %s", platform_name, exc_info=True
            )
            return f"Target validator failed for platform '{platform_name}'"
        if verdict is True:
            return None
        if isinstance(verdict, str) and verdict:
            return f"Invalid target '{target_ref}' on {platform_name}: {verdict}"
        return f"Invalid target '{target_ref}' on {platform_name}"

    if entry is not None and entry.parse_target_ref_fn is not None:
        try:
            parsed = entry.parse_target_ref_fn(target_ref)
        except Exception:
            logger.debug(
                "Plugin target parser failed for %s", platform_name, exc_info=True
            )
            return None, None, f"Target parser failed for platform '{platform_name}'"
        if parsed is not None:
            if (
                not isinstance(parsed, tuple)
                or len(parsed) != 2
                or not isinstance(parsed[0], str)
                or not parsed[0]
                or (parsed[1] is not None and not isinstance(parsed[1], str))
            ):
                return (
                    None,
                    None,
                    f"Target parser for platform '{platform_name}' returned an invalid result",
                )
            parsed_chat_id, parsed_thread_id = parsed
            error = _validate(parsed_chat_id)
            return (None, None, error) if error else (
                parsed_chat_id,
                parsed_thread_id,
                None,
            )

    parsed_chat_id, parsed_thread_id, explicit = _parse_target_ref(
        platform_name, target_ref
    )
    if explicit and parsed_chat_id is not None:
        error = _validate(parsed_chat_id)
        return (None, None, error) if error else (
            parsed_chat_id,
            parsed_thread_id,
            None,
        )

    resolution_failed = False
    try:
        from gateway.channel_directory import resolve_channel_name

        resolved = resolve_channel_name(platform_name, target_ref)
    except Exception:
        resolved = None
        resolution_failed = True
    if resolved:
        parsed_chat_id, parsed_thread_id, _ = _parse_target_ref(
            platform_name, resolved
        )
        chat_id = parsed_chat_id or resolved
        error = _validate(chat_id)
        return (None, None, error) if error else (
            chat_id,
            parsed_thread_id,
            None,
        )

    is_builtin = platform_name in {member.value for member in Platform}
    if entry is None and not is_builtin:
        return None, None, f"Unknown or unregistered plugin platform: {platform_name}"
    if entry is not None and entry.source == "plugin" and not is_builtin:
        return (
            None,
            None,
            f"Could not resolve '{target_ref}' on {platform_name}. "
            "The plugin parser did not recognize it and no channel-directory entry matched.",
        )
    hint = (
        "Try using a numeric channel ID instead."
        if resolution_failed
        else "Use send_message(action='list') to see available targets."
    )
    return None, None, f"Could not resolve '{target_ref}' on {platform_name}. {hint}"

def _describe_media_for_mirror(media_files):
    """Return a human-readable mirror summary when a message only contains media."""
    if not media_files:
        return ""
    if len(media_files) == 1:
        media_path, is_voice = media_files[0]
        ext = os.path.splitext(media_path)[1].lower()
        if is_voice and ext in _VOICE_EXTS:
            return "[Sent voice message]"
        if ext in _IMAGE_EXTS:
            return "[Sent image attachment]"
        if ext in _VIDEO_EXTS:
            return "[Sent video attachment]"
        if ext in _AUDIO_EXTS:
            return "[Sent audio attachment]"
        return "[Sent document attachment]"
    return f"[Sent {len(media_files)} media attachments]"

def _get_cron_auto_delivery_target():
    """Return the cron scheduler's auto-delivery target for the current run, if any."""
    from gateway.session_context import get_session_env
    platform = get_session_env("HERMES_CRON_AUTO_DELIVER_PLATFORM", "").strip().lower()
    chat_id = get_session_env("HERMES_CRON_AUTO_DELIVER_CHAT_ID", "").strip()
    if not platform or not chat_id:
        return None
    thread_id = get_session_env("HERMES_CRON_AUTO_DELIVER_THREAD_ID", "").strip() or None
    return {
        "platform": platform,
        "chat_id": chat_id,
        "thread_id": thread_id,
    }

def _maybe_skip_cron_duplicate_send(platform_name: str, chat_id: str, thread_id: str | None):
    """Skip redundant cron send_message calls when the scheduler will auto-deliver there."""
    auto_target = _get_cron_auto_delivery_target()
    if not auto_target:
        return None

    same_target = (
        auto_target["platform"] == platform_name
        and str(auto_target["chat_id"]) == str(chat_id)
        and auto_target.get("thread_id") == thread_id
    )
    if not same_target:
        return None

    target_label = f"{platform_name}:{chat_id}"
    if thread_id is not None:
        target_label += f":{thread_id}"

    return {
        "success": True,
        "skipped": True,
        "reason": "cron_auto_delivery_duplicate_target",
        "target": target_label,
        "note": (
            f"Skipped send_message to {target_label}. This cron job will already auto-deliver "
            "its final response to that same target. Put the intended user-facing content in "
            "your final response instead, or use a different target if you want an additional message."
        ),
    }

async def _send_via_adapter(
    platform,
    pconfig,
    chat_id,
    chunk,
    *,
    thread_id=None,
    media_files=None,
    force_document=False,
):
    """Send a message via a live gateway adapter, with a standalone fallback
    for out-of-process callers (e.g. cron running separately from the gateway).

    Order of attempts:
      1. Live in-process adapter via ``_gateway_runner_ref()`` (the path that
         existed before this change).
      2. The plugin's ``standalone_sender_fn`` registered on its
         ``PlatformEntry`` (used when the gateway is not in this process, so
         the runner weakref is ``None``).
      3. A descriptive error explaining both options.
    """
    platform_name = platform.value if hasattr(platform, "value") else str(platform)
    runner = None
    try:
        from gateway.run import _gateway_runner_ref
        runner = _gateway_runner_ref()
    except Exception:
        runner = None

    if runner is not None:
        try:
            adapter = runner.adapters.get(platform)
        except Exception:
            adapter = None
        if adapter is not None:
            try:
                metadata = {}
                if thread_id:
                    metadata["thread_id"] = thread_id
                if not metadata:
                    metadata = None
                result = await adapter.send(chat_id=chat_id, content=chunk, metadata=metadata)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                return {"error": f"Plugin platform send failed: {e}"}
            if result.success:
                return {"success": True, "message_id": result.message_id}
            return {"error": f"Adapter send failed: {result.error}"}

    entry = None
    try:
        from gateway.platform_registry import platform_registry
        entry = platform_registry.get(platform_name)
    except Exception:
        entry = None

    if entry is not None and entry.standalone_sender_fn is not None:
        try:
            result = await entry.standalone_sender_fn(
                pconfig,
                chat_id,
                chunk,
                thread_id=thread_id,
                media_files=media_files,
                force_document=force_document,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("Plugin standalone send for %s raised", platform_name, exc_info=True)
            return {"error": f"Plugin standalone send failed: {e}"}

        if isinstance(result, dict) and (result.get("success") or result.get("error")):
            return result
        return {
            "error": (
                f"Plugin standalone send for '{platform_name}' returned an "
                f"invalid result: expected a dict with 'success' or 'error' "
                f"keys, got {type(result).__name__}"
            )
        }

    return {
        "error": (
            f"No live adapter for platform '{platform_name}'. Is the gateway "
            f"running with this platform connected? For out-of-process delivery "
            f"(e.g. cron in a separate process), the platform plugin must "
            f"register a standalone_sender_fn on its PlatformEntry."
        )
    }

async def _send_to_platform(platform, pconfig, chat_id, message, thread_id=None, media_files=None, force_document=False, args=None):
    """Route a message to the appropriate platform sender.

    Long messages are automatically chunked to fit within platform limits
    using the same smart-splitting algorithm as the gateway adapters
    (preserves code-block boundaries, adds part indicators).
    """
    from gateway.config import Platform

    platform_name = platform.value if hasattr(platform, "value") else str(platform)

    media_files = media_files or []

    # Weixin handles text/media delivery inside its native helper and does not
    # need the optional platform adapter imports below. Keep this branch early
    # so a Weixin send is not blocked by unrelated optional dependencies (for
    # example lark-oapi's heavy Feishu import path).

    from gateway.platforms.base import BasePlatformAdapter, utf16_len



    media_files = media_files or []


    # Platform message length limits (from adapter class attributes for
    # built-in platforms; from PlatformEntry.max_message_length for plugins,
    # resolved via the registry fallback below — covers Slack and Feishu, both
    # migrated to plugins in #41112).
    # Telegram was the only built-in platform with a hard length cap; it was
    # removed from this fork. Plugin platforms resolve their own caps via the
    # registry fallback below.
    _MAX_LENGTHS = {}

    # Check plugin registry for max_message_length
    if platform not in _MAX_LENGTHS:
        try:
            from gateway.platform_registry import platform_registry
            entry = platform_registry.get(platform.value)
            if entry and entry.max_message_length > 0:
                _MAX_LENGTHS[platform] = entry.max_message_length
        except Exception:
            pass

    # Smart-chunk the message to fit within platform limits.
    # For short messages or platforms without a known limit this is a no-op.
    max_len = _MAX_LENGTHS.get(platform)
    if max_len:
        _len_fn = None
        chunks = BasePlatformAdapter.truncate_message(message, max_len, len_fn=_len_fn)
    else:
        chunks = [message]


    # --- Non-media platforms ---
    if media_files and not message.strip():
        return {
            "error": (
                f"send_message MEDIA delivery is not supported for this platform; "
                f"target {platform.value} had only media attachments"
            )
        }
    warning = None
    if media_files:
        warning = (
            f"MEDIA attachments were omitted for {platform.value}; "
            "native send_message media delivery is not supported for this platform"
        )

    last_result = None
    for chunk in chunks:
        if platform == Platform.EMAIL:
            result = await _registry_standalone_send(
                "email", pconfig, chat_id, chunk, thread_id=thread_id
            )
        else:
            from gateway.platform_registry import platform_registry

            entry = platform_registry.get(platform_name)
            handler = entry.send_message_handler if entry is not None else None
            if handler is not None:
                try:
                    import inspect

                    result = handler(args or {}, chat_id, platform_name, pconfig)
                    if inspect.isawaitable(result):
                        result = await result
                    return result
                except Exception as e:
                    return {"error": f"Plugin send_message handler failed: {e}"}
            # Plugin platform: route through the gateway's live adapter if
            # available, otherwise the plugin's standalone_sender_fn.
            result = await _send_via_adapter(
                platform,
                pconfig,
                chat_id,
                chunk,
                thread_id=thread_id,
                media_files=media_files,
                force_document=force_document,
            )

        if isinstance(result, dict) and result.get("error"):
            return result
        last_result = result

    if warning and isinstance(last_result, dict) and last_result.get("success"):
        warnings = list(last_result.get("warnings", []))
        warnings.append(warning)
        last_result["warnings"] = warnings
    return last_result

async def _registry_standalone_send(platform_name, pconfig, chat_id, message, thread_id=None):
    """Dispatch a one-shot send through a migrated platform plugin's
    standalone_sender_fn (registry hook).  Used for platforms whose adapter
    moved out of gateway/platforms/ into plugins/platforms/<name>/ (#41112):
    the legacy inline ``_send_<platform>`` helper now lives in the plugin as
    ``_standalone_send`` and is reached via the platform registry.
    """
    from gateway.platform_registry import platform_registry
    from hermes_cli.plugins import discover_plugins
    discover_plugins()  # idempotent — ensure the entry is registered
    entry = platform_registry.get(platform_name)
    if entry is None or entry.standalone_sender_fn is None:
        return {"error": f"{platform_name} plugin not registered or missing standalone_sender_fn"}
    return await entry.standalone_sender_fn(pconfig, chat_id, message, thread_id=thread_id)

#41112.

