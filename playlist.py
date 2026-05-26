import asyncio
from pyrogram import filters, Client
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

import config
import playlist_manager
from state import app, allowed, LOG, user_tasks, user_setup
from constants import MAX_CONCURRENT
from utils import detect_stream_info, format_quality_line
from keyboards import (
    build_main_keyboard, build_audio_keyboard,
    build_watermark_keyboard, setup_summary_text,
)


@app.on_message(filters.command("playlistadd") & allowed)
async def playlistadd_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        return await message.reply_text(
            "❌ **Usage:** `/playlistadd <url> [name]`\n\n"
            "**Example:**\n"
            "`/playlistadd https://play.ksrtech.fun/playlist.php?token=KSR-xxx MyList`"
        )

    url  = args[0]
    name = " ".join(args[1:]).strip() if len(args) > 1 else \
           f"Playlist{len(playlist_manager.get_playlists(message.from_user.id)) + 1}"

    msg = await message.reply_text("🔍 **Checking playlist URL...**")

    ok, err, channels = await playlist_manager.fetch_and_parse(url)
    if not ok:
        return await msg.edit_text(f"❌ **Invalid Playlist!**\n\n`{err}`")

    groups = playlist_manager.get_groups(channels)
    success, result_msg = playlist_manager.add_playlist(message.from_user.id, name, url)

    if success:
        playlist_manager.cache_set(
            message.from_user.id,
            len(playlist_manager.get_playlists(message.from_user.id)) - 1,
            channels,
        )

    await msg.edit_text(
        f"{result_msg}\n\n"
        f"📺 **Channels:** `{len(channels)}`\n"
        f"📂 **Groups:** `{len(groups)}`\n"
        f"🔗 **URL:** `{url[:60]}{'...' if len(url) > 60 else ''}`\n\n"
        f"Use /channel to browse channels."
    )


@app.on_message(filters.command("playlistdelete") & allowed)
async def playlistdelete_cmd(client: Client, message: Message):
    user_id   = message.from_user.id
    playlists = playlist_manager.get_playlists(user_id)

    if not playlists:
        return await message.reply_text("📭 **No playlists saved.** Add one with /playlistadd")

    args = message.command[1:]
    if not args:
        names = "\n".join(f"  • `{p['name']}`" for p in playlists)
        return await message.reply_text(
            f"❌ **Usage:** `/playlistdelete <name>`\n\n"
            f"**Your playlists:**\n{names}"
        )

    name = " ".join(args).strip()
    success, result_msg = playlist_manager.delete_playlist(user_id, name)
    await message.reply_text(result_msg)


@app.on_message(filters.command("channel") & allowed)
async def channel_cmd(client: Client, message: Message):
    user_id   = message.from_user.id
    playlists = playlist_manager.get_playlists(user_id)

    if not playlists:
        return await message.reply_text(
            "📭 **No playlists saved yet!**\n\n"
            "Add one first:\n"
            "`/playlistadd <url> [name]`\n\n"
            "**Example:**\n"
            "`/playlistadd https://play.ksrtech.fun/playlist.php?token=KSR-xxx MyList`"
        )

    buttons = [
        [InlineKeyboardButton(f"📋 {p['name']}", callback_data=f"plg_{i}")]
        for i, p in enumerate(playlists)
    ]
    await message.reply_text(
        "📺 **Select a Playlist:**",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@app.on_callback_query(filters.regex(r"^plg_(\d+)$"))
async def cb_playlist_groups(client: Client, query):
    user_id   = query.from_user.id
    pl_idx    = int(query.matches[0].group(1))
    playlists = playlist_manager.get_playlists(user_id)

    if pl_idx >= len(playlists):
        return await query.answer("Playlist not found!", show_alert=True)

    pl = playlists[pl_idx]
    await query.answer()
    await query.message.edit_text(f"⏳ **Loading `{pl['name']}`...**")

    channels = playlist_manager.cache_get(user_id, pl_idx)
    if not channels:
        ok, err, channels = await playlist_manager.fetch_and_parse(pl["url"])
        if not ok:
            return await query.message.edit_text(f"❌ **Failed to load playlist:**\n`{err}`")
        playlist_manager.cache_set(user_id, pl_idx, channels)

    groups  = playlist_manager.get_groups(channels)
    buttons = []
    row = []
    for gi, g in enumerate(groups):
        count = len(playlist_manager.channels_in_group(channels, g))
        row.append(InlineKeyboardButton(f"{g} ({count})", callback_data=f"pgg_{pl_idx}_{gi}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="pl_back")])

    await query.message.edit_text(
        f"📂 **{pl['name']}** — Select a group:\n"
        f"📺 Total `{len(channels)}` channels in `{len(groups)}` groups",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@app.on_callback_query(filters.regex(r"^pgg_(\d+)_(\d+)$"))
async def cb_group_channels(client: Client, query):
    user_id   = query.from_user.id
    pl_idx    = int(query.matches[0].group(1))
    grp_idx   = int(query.matches[0].group(2))
    playlists = playlist_manager.get_playlists(user_id)

    if pl_idx >= len(playlists):
        return await query.answer("Playlist not found!", show_alert=True)

    channels = playlist_manager.cache_get(user_id, pl_idx)
    if not channels:
        ok, err, channels = await playlist_manager.fetch_and_parse(playlists[pl_idx]["url"])
        if not ok:
            return await query.answer("Failed to load playlist.", show_alert=True)
        playlist_manager.cache_set(user_id, pl_idx, channels)

    groups = playlist_manager.get_groups(channels)
    if grp_idx >= len(groups):
        return await query.answer("Group not found!", show_alert=True)

    group_name  = groups[grp_idx]
    chs         = playlist_manager.channels_in_group(channels, group_name)
    page_size   = 20
    total_pages = (len(chs) - 1) // page_size + 1

    await query.answer()
    buttons = []
    for ci, ch in enumerate(chs[:page_size]):
        buttons.append([InlineKeyboardButton(
            f"📡 {ch['name']}", callback_data=f"plc_{pl_idx}_{grp_idx}_{ci}"
        )])

    nav = []
    if total_pages > 1:
        nav.append(InlineKeyboardButton(f"▶ Next (1/{total_pages})", callback_data=f"pgp_{pl_idx}_{grp_idx}_1"))
    nav.append(InlineKeyboardButton("🔙 Back", callback_data=f"plg_{pl_idx}"))
    buttons.append(nav)

    await query.message.edit_text(
        f"📡 **{group_name}** — {len(chs)} channels\nTap a channel to record:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@app.on_callback_query(filters.regex(r"^pgp_(\d+)_(\d+)_(\d+)$"))
async def cb_channels_page(client: Client, query):
    user_id   = query.from_user.id
    pl_idx    = int(query.matches[0].group(1))
    grp_idx   = int(query.matches[0].group(2))
    page      = int(query.matches[0].group(3))
    playlists = playlist_manager.get_playlists(user_id)

    channels = playlist_manager.cache_get(user_id, pl_idx)
    if not channels:
        ok, err, channels = await playlist_manager.fetch_and_parse(playlists[pl_idx]["url"])
        if not ok:
            return await query.answer("Failed to load playlist.", show_alert=True)
        playlist_manager.cache_set(user_id, pl_idx, channels)

    groups      = playlist_manager.get_groups(channels)
    group_name  = groups[grp_idx]
    chs         = playlist_manager.channels_in_group(channels, group_name)
    page_size   = 20
    total_pages = (len(chs) - 1) // page_size + 1
    page        = max(0, min(page, total_pages - 1))
    start       = page * page_size

    await query.answer()
    buttons = []
    for ci, ch in enumerate(chs[start:start + page_size]):
        real_idx = start + ci
        buttons.append([InlineKeyboardButton(
            f"📡 {ch['name']}", callback_data=f"plc_{pl_idx}_{grp_idx}_{real_idx}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"pgp_{pl_idx}_{grp_idx}_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶ Next", callback_data=f"pgp_{pl_idx}_{grp_idx}_{page + 1}"))
    nav.append(InlineKeyboardButton("🔙 Back", callback_data=f"plg_{pl_idx}"))
    buttons.append(nav)

    await query.message.edit_text(
        f"📡 **{group_name}** — Page {page + 1}/{total_pages}:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@app.on_callback_query(filters.regex(r"^plc_(\d+)_(\d+)_(\d+)$"))
async def cb_channel_selected(client: Client, query):
    user_id   = query.from_user.id
    pl_idx    = int(query.matches[0].group(1))
    grp_idx   = int(query.matches[0].group(2))
    ch_idx    = int(query.matches[0].group(3))
    playlists = playlist_manager.get_playlists(user_id)

    channels = playlist_manager.cache_get(user_id, pl_idx)
    if not channels:
        ok, err, channels = await playlist_manager.fetch_and_parse(playlists[pl_idx]["url"])
        if not ok:
            return await query.answer("Failed to load playlist.", show_alert=True)
        playlist_manager.cache_set(user_id, pl_idx, channels)

    groups     = playlist_manager.get_groups(channels)
    group_name = groups[grp_idx]
    chs        = playlist_manager.channels_in_group(channels, group_name)

    if ch_idx >= len(chs):
        return await query.answer("Channel not found!", show_alert=True)

    ch         = chs[ch_idx]
    stream_url = ch["url"]
    safe_name  = ch["name"].replace("`", "'")[:40] or config.DEFAULT_FILENAME
    timestamp  = "01:00:00"

    await query.answer()

    if len(user_tasks.get(user_id, {})) >= MAX_CONCURRENT:
        return await query.message.reply_text(
            f"❌ **Maximum {MAX_CONCURRENT} simultaneous recordings reached!**\n"
            f"📊 /status  |  🛑 /cancel",
            reply_markup=build_main_keyboard(user_id)
        )

    await query.message.edit_text(
        f"📡 **{ch['name']}**\n"
        f"📂 Group: `{ch.get('group', 'General')}`\n\n"
        f"🔍 Stream detect ho rahi hai, please wait...",
        reply_markup=None
    )

    try:
        info = await detect_stream_info(stream_url)
    except Exception as e:
        LOG.error(f"playlist detect_stream_info error: {e}")
        return await query.message.edit_text(
            f"❌ **Stream detect failed!**\n\n`{e}`\n\n"
            "Channel URL check karein ya doosra channel try karein.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data=f"pgg_{pl_idx}_{grp_idx}")
            ]])
        )

    tracks   = info["tracks"]
    video    = info["video"]
    selected = set(t["index"] for t in tracks)

    user_setup[user_id] = {
        "mode":            "record",
        "step":            "audio" if tracks else "watermark",
        "url":             stream_url,
        "timestamp":       timestamp,
        "filename":        safe_name,
        "tracks":          tracks,
        "selected_tracks": selected,
        "watermark_pos":   None,
        "watermark_text":  config.DEFAULT_FILENAME,
        "auto_mode":       False,
        "video_size":      "original",
        "chat_id":         query.message.chat.id,
        "reply_to":        query.message.id,
        "video_info":      video,
    }

    quality_line = format_quality_line(video)
    audio_line   = ", ".join(t["label"] for t in tracks) if tracks else "Auto"

    if tracks:
        text = (
            f"✅ **Stream Ready!**\n\n"
            f"📡 **Channel:** `{ch['name']}`\n"
            f"📺 **Quality:** `{quality_line}`\n"
            f"🎵 **Audio:** `{audio_line}`\n"
            f"⏱ **Duration:** `{timestamp}`\n"
            f"📁 **File:** `{safe_name}`\n\n"
            f"👇 Select audio tracks to include:"
        )
        kb = build_audio_keyboard(tracks, selected)
    else:
        text = (
            f"✅ **Stream Ready!**\n\n"
            f"📡 **Channel:** `{ch['name']}`\n"
            f"📺 **Quality:** `{quality_line}`\n"
            f"🎵 **Audio:** No tracks — auto-select\n\n"
        ) + setup_summary_text(user_setup[user_id])
        kb = build_watermark_keyboard(user_setup[user_id])

    await query.message.reply_text(text, reply_markup=kb)


@app.on_callback_query(filters.regex(r"^pl_back$"))
async def cb_pl_back(client: Client, query):
    user_id   = query.from_user.id
    playlists = playlist_manager.get_playlists(user_id)
    await query.answer()

    if not playlists:
        return await query.message.edit_text("📭 No playlists saved.")

    buttons = [
        [InlineKeyboardButton(f"📋 {p['name']}", callback_data=f"plg_{i}")]
        for i, p in enumerate(playlists)
    ]
    await query.message.edit_text(
        "📺 **Select a Playlist:**",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
