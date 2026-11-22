-- German Word Capture for mpv
--
-- Ctrl+W  -> pauses and opens the capture panel on the current subtitle.
--   1. Pick   : type to filter the subtitle's words, Up/Down to move,
--               Tab to mark several words (separable verbs, phrases), Enter.
--   2. Edit   : fix the word (e.g. turn "schließe ... zu" into "zuschließen"), Enter.
--   3. Meaning: optional note/translation, Enter saves (empty is fine).
-- Esc goes back one step (closes on step 1).
--
-- Ctrl+E  -> the same panel in sentence mode (SENTENCE_HOTKEY):
--   1. Pick   : Tab marks words; they are joined in the order you marked them
--               (marked words show their number). Nothing marked: the whole line.
--   2. Edit   : fix the sentence, Enter.
--   3. English: the translation (required), Enter saves.
--   The row (German | English | screenshot | audio) goes to SENTENCE_FILE and is sent to
--   `german-anki add-sentence`; a new card moves it to SENTENCE_DONE_FILE.
--
-- On save a row is appended to MARKDOWN_FILE with:
--   word | meaning | screenshot (with subtitles) | audio clip of the subtitle line
-- The audio is cut with ffmpeg in the background from the active audio track.
-- The row is then sent to `german-anki add-row` in the background. A new card moves the
-- row to DONE_FILE; a word already in Anki (media filled in / already complete) just
-- removes it from MARKDOWN_FILE; on failure it stays in MARKDOWN_FILE.

local mp    = require("mp")
local msg   = require("mp.msg")
local utils = require("mp.utils")

-- ============================================================
-- CONFIGURATION
-- ============================================================

-- ------------------------------------------------------------ shared config
-- ~/.config/german-anki/.env (or $GERMAN_ANKI_CONFIG), the same file the Python tools read.
local function config_reader()
    local home = os.getenv("HOME") or ""
    local xdg = os.getenv("XDG_CONFIG_HOME")
    local path = os.getenv("GERMAN_ANKI_CONFIG")
        or (((xdg and xdg ~= "") and xdg or (home .. "/.config")) .. "/german-anki/.env")
    local cfg = {}
    local f = io.open(path, "r")
    if f then
        for line in f:lines() do
            if not line:match("^%s*#") then
                local k, v = line:match("^%s*([%w_]+)%s*=%s*(.-)%s*$")
                if k then cfg[k] = (v:gsub('^"(.*)"$', "%1"):gsub("^'(.*)'$", "%1")) end
            end
        end
        f:close()
    end
    local get = {}
    function get.str(key, default)
        local v = cfg[key]
        if v == nil or v == "" then v = default end
        return (v:gsub("^~", home))
    end
    function get.num(key, default) return tonumber(cfg[key] or "") or default end
    function get.bool(key, default)
        local v = (cfg[key] or ""):lower()
        if v == "" then return default end
        return v == "yes" or v == "true" or v == "1" or v == "on"
    end
    return get
end
local conf = config_reader()

local BASE_DIR      = conf.str("GERMAN_ANKI_DATA_DIR", "~/.local/share/german-anki")
local MARKDOWN_FILE = BASE_DIR .. "/To-do-german-words.md"
local ASSET_DIR     = BASE_DIR .. "/assets"
local DONE_FILE     = BASE_DIR .. "/To-do-german-words-done.md"
local SENTENCE_FILE      = BASE_DIR .. "/sentence-mining.md"
local SENTENCE_DONE_FILE = BASE_DIR .. "/sentence-mining-done.md"

-- Every saved row is sent to `german-anki add-row` (one at a time, in the background). A new
-- card moves the row from MARKDOWN_FILE to DONE_FILE; a word that was already in Anki (missing
-- media filled in, or already complete) is only removed from MARKDOWN_FILE; when it fails the
-- row stays in MARKDOWN_FILE. CAPTURE_AUTO_ADD=no disables this.
local GERMAN_ANKI   = conf.bool("CAPTURE_AUTO_ADD", true)
                      and conf.str("GERMAN_ANKI_BIN", "~/.local/bin/german-anki") or nil

local IMAGE_SIZE    = conf.str("CAPTURE_IMAGE_SIZE", "500x379")   -- Obsidian embed size hint
local HOTKEY        = conf.str("CAPTURE_HOTKEY", "ctrl+w")
local SENTENCE_HOTKEY = conf.str("SENTENCE_HOTKEY", "ctrl+e")
local SENTENCE_SLUG_WORDS = 4     -- media files are named after the first words of the sentence

local AUDIO_PAD_BEFORE = 0.25     -- seconds of audio kept before the subtitle starts
local AUDIO_PAD_AFTER  = 0.40     -- ... and after it ends
local AUDIO_FALLBACK   = { 4.0, 1.0 } -- no sub timing: take [now-4s, now+1s]
local AUDIO_MAX_LEN    = 20.0     -- safety cap for very long subtitle events

local MAX_VISIBLE      = 7        -- words shown in the list at once

-- Colors (ASS uses BGR hex)
local C = {
    panel   = "1A1A1E", panel_a = "0C",
    field   = "2C2C33",
    accent  = "3CC8FF",   -- warm amber
    text    = "F2F2F2",
    dim     = "9A9AA3",
    faint   = "5E5E66",
    ok      = "7CDB7C",
    err     = "6B6BFF",
    sel_bg  = "3A3A44",
}

-- ============================================================
-- STATE
-- ============================================================

local overlay = mp.create_osd_overlay("ass-events")
local toast   = mp.create_osd_overlay("ass-events")
toast.z = 10
local toast_timer = nil

local state = "inactive"          -- inactive | selecting | editing_word | typing
local mode = "word"               -- word (Ctrl+W) | sentence (Ctrl+E)
local capture_counter = 0

local tokens = {}                 -- { {raw=, word=}, ... } from the subtitle
local sentence = ""
local filtered = {}               -- indices into tokens
local sel = 1                     -- index into filtered
local scroll = 1
local marked = {}                 -- token index -> true
local mark_order = {}             -- token indices in the order they were marked (sentence mode)

local chosen_word = ""
local input_chars, cursor_pos = {}, 1
local active_bindings = {}
local was_paused = false
local snapshot = nil              -- media info captured when the panel opened

-- ============================================================
-- HELPERS
-- ============================================================

local function trim(s) return (tostring(s or ""):gsub("^%s+", ""):gsub("%s+$", "")) end

local function ass_escape(s)
    s = tostring(s or "")
    s = s:gsub("\\", "\\\239\187\191"):gsub("{", "\\{"):gsub("}", "\\}")
    s = s:gsub("\r?\n", " ")
    return s
end

local function utf8_chars(str)
    local t = {}
    for ch in tostring(str):gmatch("[%z\1-\127\194-\244][\128-\191]*") do t[#t + 1] = ch end
    return t
end

local function utf8_len(s) return #utf8_chars(s) end

local function lower(s)
    return (tostring(s):lower():gsub("Ä", "ä"):gsub("Ö", "ö"):gsub("Ü", "ü"))
end

local function markdown_cell(text)
    return (tostring(text or ""):gsub("|", "\\|"):gsub("\r?\n", " "))
end

-- "Störung" -> "störung", "wachsen (sein)" -> "wachsen-sein" (same rule as rename_media.py)
local function word_slug(word)
    local lower_map = { ["Ä"] = "ä", ["Ö"] = "ö", ["Ü"] = "ü", ["ẞ"] = "ß" }
    local out = {}
    for ch in tostring(word):gmatch("[%z\1-\127\194-\244][\128-\191]*") do
        ch = lower_map[ch] or ch:lower()
        if ch:match("^[%w]$") or ch == "ä" or ch == "ö" or ch == "ü" or ch == "ß" then
            out[#out + 1] = ch
        elseif ch:match("^[%s/%-]$") then
            out[#out + 1] = "-"
        end
    end
    local s = table.concat(out):gsub("%-+", "-"):gsub("^%-", ""):gsub("%-$", "")
    return s ~= "" and s or "word"
end

-- First <slug>-v<N> with no existing screenshot/audio file in ASSET_DIR.
local function next_versioned_name(word)
    local base = word_slug(word)
    local n = 1
    while true do
        local name = string.format("%s-v%d", base, n)
        local used = false
        for _, ext in ipairs({ "webp", "png", "jpg", "ogg", "opus", "mp3" }) do
            if utils.file_info(ASSET_DIR .. "/" .. name .. "." .. ext) then used = true; break end
        end
        if not used then return name end
        n = n + 1
    end
end

local function make_id()
    capture_counter = (capture_counter + 1) % 1000
    local ms = math.floor((mp.get_time() % 1) * 1000)
    return string.format("%d%03d%03d", os.time(), ms, capture_counter)
end

local PUNCT = "[%.,!?;:%(%)%[%]%{%}\"'„“”‚‘’«»…]"
local function clean_word(w)
    w = tostring(w or "")
    w = w:gsub("^" .. PUNCT .. "+", ""):gsub(PUNCT .. "+$", "")
    w = w:gsub("^[-–—]+", ""):gsub("[-–—]+$", "")
    return w
end

local function file_exists(path)
    return utils.file_info(path) ~= nil
end

local function ensure_dirs()
    local r = utils.subprocess({ args = { "mkdir", "-p", ASSET_DIR }, cancellable = false })
    return r.status == 0
end

local function append_markdown(row, file)
    file = file or MARKDOWN_FILE
    local new_file = not file_exists(file)
    local f, err = io.open(file, "a")
    if not f then return false, tostring(err) end
    if new_file then
        f:write("| German | English | Image | Audio |\n")
        f:write("| ------ | ------- | ----- | ----- |\n")
    end
    f:write(row)
    f:close()
    return true
end

-- ============================================================
-- TOAST (feedback after closing the panel)
-- ============================================================

local rounded_rect -- defined below

local function show_toast(lines, seconds)
    local w, h = mp.get_osd_size()
    if not w or w == 0 then w, h = 1280, 720 end
    local s = math.max(0.6, h / 720)
    local fs = math.floor(19 * s)
    local pad = math.floor(14 * s)
    local lh = math.floor(fs * 1.4)
    local maxlen = 0
    for _, l in ipairs(lines) do
        local plain = l:gsub("{[^}]*}", "")
        local n = 0
        for _ in plain:gmatch("[%z\1-\127\194-\244][\128-\191]*") do n = n + 1 end
        if n > maxlen then maxlen = n end
    end
    local bw = math.floor(maxlen * fs * 0.5) + 2 * pad
    local bh = #lines * lh + 2 * pad - math.floor(lh - fs)
    local x = w - bw - math.floor(24 * s)
    local y = math.floor(24 * s)
    local out = { string.format("{\\an7\\pos(0,0)\\bord0\\shad0\\1c&H%s&\\1a&H0C&\\p1}%s{\\p0}",
        C.panel, rounded_rect(x, y, bw, bh, math.floor(10 * s))) }
    for i, l in ipairs(lines) do
        out[#out + 1] = string.format("{\\an7\\pos(%d,%d)\\fs%d\\bord0\\shad0\\1c&H%s&\\q2}%s",
            x + pad, y + pad + (i - 1) * lh, fs, C.text, l)
    end
    toast.res_x, toast.res_y = w, h
    toast.data = table.concat(out, "\n")
    toast:update()
    if toast_timer then toast_timer:kill() end
    toast_timer = mp.add_timeout(seconds or 3, function() toast:remove() end)
end

local function tag(color, text) return "{\\c&H" .. color .. "&}" .. text .. "{\\c&H" .. C.text .. "&}" end

-- ============================================================
-- MEDIA CAPTURE
-- ============================================================

local function take_snapshot()
    local snap = {}
    local now = mp.get_property_number("time-pos", 0)
    local s0, s1 = mp.get_property_number("sub-start"), mp.get_property_number("sub-end")
    if s0 and s1 and s1 > s0 then
        snap.a_start = math.max(0, s0 - AUDIO_PAD_BEFORE)
        snap.a_end = s1 + AUDIO_PAD_AFTER
    else
        snap.a_start = math.max(0, now - AUDIO_FALLBACK[1])
        snap.a_end = now + AUDIO_FALLBACK[2]
    end
    if snap.a_end - snap.a_start > AUDIO_MAX_LEN then snap.a_end = snap.a_start + AUDIO_MAX_LEN end

    -- Which file/stream holds the active audio?
    local aid = mp.get_property_native("current-tracks/audio")
    if aid then
        if aid.external and aid["external-filename"] then
            snap.a_src, snap.a_map = aid["external-filename"], "0:a:0"
        else
            local path = mp.get_property("path", "")
            if path:match("^%a[%w+.-]*://") then
                -- network stream: use the resolved URL unless it is an EDL/compound stream
                local s = mp.get_property("stream-open-filename", "")
                if s ~= "" and not s:match("^edl://") then path = s else path = nil end
            elseif path ~= "" and not path:match("^/") then
                path = utils.join_path(mp.get_property("working-directory", ""), path)
            end
            snap.a_src = path
            snap.a_map = aid["ff-index"] and ("0:" .. aid["ff-index"]) or "0:a:0"
        end
    end
    return snap
end

local function extract_audio(snap, out_path, on_done)
    if not snap.a_src then on_done(false, "no audio source"); return end
    local args = {
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", string.format("%.3f", snap.a_start),
        "-t", string.format("%.3f", snap.a_end - snap.a_start),
        "-i", snap.a_src,
        "-map", snap.a_map, "-vn", "-sn", "-dn",
        "-ac", "1", "-c:a", "libopus", "-b:a", "24k", "-application", "voip",
        out_path,
    }
    mp.command_native_async({ name = "subprocess", args = args, playback_only = false,
                              capture_stderr = true },
        function(success, res)
            local ok = success and res and res.status == 0 and file_exists(out_path)
            if not ok then
                msg.error("ffmpeg failed: " .. tostring(res and res.stderr or res and res.error_string))
            end
            on_done(ok, ok and nil or trim(res and res.stderr or "ffmpeg failed"))
        end)
end

-- ============================================================
-- SEARCH
-- ============================================================

local function rank(word, q)
    if q == "" then return 1 end
    local w = lower(word)
    if w:sub(1, #q) == q then return 1 end
    if w:find(q, 1, true) then return 2 end
    local qi = 1
    for i = 1, #w do
        if w:byte(i) == q:byte(qi) then
            qi = qi + 1
            if qi > #q then return 3 end
        end
    end
    return nil
end

local function update_filter()
    local q = lower(table.concat(input_chars))
    local scored = {}
    for i, t in ipairs(tokens) do
        local r = rank(t.word, q)
        if r then scored[#scored + 1] = { i = i, r = r } end
    end
    table.sort(scored, function(a, b) if a.r ~= b.r then return a.r < b.r end return a.i < b.i end)
    filtered = {}
    for _, s in ipairs(scored) do filtered[#filtered + 1] = s.i end
    sel, scroll = 1, 1
end

local function current_token()
    return filtered[sel]
end

-- ============================================================
-- RENDERING
-- ============================================================

function rounded_rect(x, y, w, h, r)
    local k = r * 0.45
    return string.format(
        "m %d %d l %d %d b %d %d %d %d %d %d l %d %d b %d %d %d %d %d %d " ..
        "l %d %d b %d %d %d %d %d %d l %d %d b %d %d %d %d %d %d",
        x + r, y, x + w - r, y, x + w - k, y, x + w, y + k, x + w, y + r,
        x + w, y + h - r, x + w, y + h - k, x + w - k, y + h, x + w - r, y + h,
        x + r, y + h, x + k, y + h, x, y + h - k, x, y + h - r,
        x, y + r, x, y + k, x + k, y, x + r, y)
end

local function draw_box(x, y, w, h, r, color, alpha)
    return string.format("{\\an7\\pos(0,0)\\bord0\\shad0\\1c&H%s&\\1a&H%s&\\p1}%s{\\p0}",
        color, alpha or "00", rounded_rect(x, y, w, h, r))
end

local function draw_text(x, y, fs, text, an)
    return string.format("{\\an%d\\pos(%d,%d)\\fs%d\\bord0\\shad0\\1c&H%s&\\q2}%s",
        an or 7, x, y, fs, C.text, text)
end

-- Greedy word wrap on display characters (approximate glyph width).
local function wrap_tokens(parts, max_chars)
    local lines, line, len = {}, {}, 0
    for _, p in ipairs(parts) do
        local l = utf8_len(p.plain)
        if len > 0 and len + 1 + l > max_chars then
            lines[#lines + 1] = table.concat(line, " ")
            line, len = {}, 0
        end
        line[#line + 1] = p.styled
        len = len + l + (len > 0 and 1 or 0)
    end
    if #line > 0 then lines[#lines + 1] = table.concat(line, " ") end
    return lines
end

local function input_text(placeholder)
    if #input_chars == 0 and placeholder then
        return tag(C.accent, "▏") .. tag(C.faint, ass_escape(placeholder))
    end
    local before = ass_escape(table.concat(input_chars, "", 1, cursor_pos - 1))
    local after  = ass_escape(table.concat(input_chars, "", cursor_pos, #input_chars))
    return before .. tag(C.accent, "▏") .. after
end

local function render()
    if state == "inactive" then overlay:remove(); return end

    local W, H = mp.get_osd_size()
    if not W or W == 0 then W, H = 1280, 720 end
    overlay.res_x, overlay.res_y = W, H

    local s   = math.max(0.6, H / 720)
    local fs  = math.floor(22 * s)
    local sfs = math.floor(16 * s)
    local pad = math.floor(22 * s)
    local pw  = math.min(math.floor(W * 0.62), math.floor(760 * s))
    pw = math.max(pw, math.min(W - 20, math.floor(420 * s)))
    local px  = math.floor((W - pw) / 2)
    local py  = math.floor(36 * s)
    local inner = pw - 2 * pad
    local line_h = math.floor(fs * 1.45)
    local max_chars = math.max(12, math.floor(inner / (fs * 0.52)))

    local out = {}
    local y = py + pad

    -- Header: title + step indicator
    local sm = (mode == "sentence")
    local steps = { { "selecting", "1 Pick" }, { "editing_word", "2 Edit" },
                    { "typing", sm and "3 English" or "3 Meaning" } }
    local step_txt = {}
    for _, st in ipairs(steps) do
        if st[1] == state then
            step_txt[#step_txt + 1] = "{\\b1}" .. tag(C.accent, st[2]) .. "{\\b0}"
        else
            step_txt[#step_txt + 1] = tag(C.faint, st[2])
        end
    end
    out[#out + 1] = draw_text(px + pad, y, sfs, "{\\b1}" .. tag(C.accent, sm and "SENTENCE CAPTURE" or "WORD CAPTURE") .. "{\\b0}")
    out[#out + 1] = draw_text(px + pw - pad, y, sfs, table.concat(step_txt, tag(C.faint, "  ›  ")), 9)
    y = y + math.floor(sfs * 2.0)

    -- Sentence with highlights
    local hl = (state == "selecting") and current_token() or nil
    local order_of = {}
    for k, i in ipairs(mark_order) do order_of[i] = k end
    local parts = {}
    for i, t in ipairs(tokens) do
        local txt = ass_escape(t.raw)
        if marked[i] then
            txt = "{\\b1}" .. tag(C.ok, txt) .. "{\\b0}"
            if sm and order_of[i] then txt = tag(C.ok, "{\\fscx70\\fscy70}" .. order_of[i] .. "{\\fscx100\\fscy100}") .. txt end
        elseif i == hl then
            txt = "{\\b1\\u1}" .. tag(C.accent, txt) .. "{\\u0\\b0}"
        elseif state == "selecting" then
            txt = tag(C.dim, txt)
        end
        parts[#parts + 1] = { plain = t.raw, styled = txt }
    end
    local sent_lines = wrap_tokens(parts, max_chars)
    for _, l in ipairs(sent_lines) do
        out[#out + 1] = draw_text(px + pad, y, fs, l)
        y = y + line_h
    end
    y = y + math.floor(10 * s)

    -- Input field
    local labels = {
        selecting    = "Search",
        editing_word = sm and "Sentence" or "Word",
        typing       = sm and "English" or "Meaning",
    }
    local placeholders = {
        selecting    = "type to filter…",
        editing_word = nil,
        typing       = sm and "required — the translation" or "optional — Enter to skip",
    }
    local fh = math.floor(fs * 1.9)
    out[#out + 1] = draw_box(px + pad, y, inner, fh, math.floor(8 * s), C.field)
    out[#out + 1] = draw_text(px + pad + math.floor(14 * s), y + math.floor(fh / 2), sfs,
        tag(C.dim, labels[state]), 4)
    local label_w = math.floor(sfs * 0.6 * 8)
    out[#out + 1] = draw_text(px + pad + math.floor(14 * s) + label_w, y + math.floor(fh / 2), fs,
        input_text(placeholders[state]), 4)
    y = y + fh + math.floor(12 * s)

    -- Step-specific body
    if state == "selecting" then
        if #filtered == 0 then
            out[#out + 1] = draw_text(px + pad, y, fs, tag(C.faint, "No matching words"))
            y = y + line_h
        else
            if sel < scroll then scroll = sel end
            if sel >= scroll + MAX_VISIBLE then scroll = sel - MAX_VISIBLE + 1 end
            local last = math.min(#filtered, scroll + MAX_VISIBLE - 1)
            if scroll > 1 then
                out[#out + 1] = draw_text(px + pad, y, sfs, tag(C.faint, "  ▲ " .. (scroll - 1) .. " more"))
                y = y + math.floor(sfs * 1.4)
            end
            local row_h = math.floor(fs * 1.55)
            for k = scroll, last do
                local ti = filtered[k]
                local word = ass_escape(tokens[ti].word)
                local mark = marked[ti] and tag(C.ok, (sm and order_of[ti]) and (order_of[ti] .. " ") or "✓ ") or "   "
                if k == sel then
                    out[#out + 1] = draw_box(px + pad, y, inner, row_h, math.floor(6 * s), C.sel_bg)
                    out[#out + 1] = draw_box(px + pad, y + math.floor(row_h * 0.2), math.floor(4 * s),
                        math.floor(row_h * 0.6), math.floor(2 * s), C.accent)
                    out[#out + 1] = draw_text(px + pad + math.floor(16 * s), y + math.floor(row_h / 2), fs,
                        mark .. "{\\b1}" .. word .. "{\\b0}", 4)
                else
                    out[#out + 1] = draw_text(px + pad + math.floor(16 * s), y + math.floor(row_h / 2), fs,
                        mark .. tag(C.dim, word), 4)
                end
                y = y + row_h
            end
            if last < #filtered then
                out[#out + 1] = draw_text(px + pad, y, sfs, tag(C.faint, "  ▼ " .. (#filtered - last) .. " more"))
                y = y + math.floor(sfs * 1.4)
            end
        end
        local n_marked = 0
        for _ in pairs(marked) do n_marked = n_marked + 1 end
        if n_marked > 0 then
            y = y + math.floor(4 * s)
            out[#out + 1] = draw_text(px + pad, y, sfs, tag(C.ok, n_marked .. " word(s) marked — Enter joins them" ..
                (sm and " in this order" or "")))
            y = y + math.floor(sfs * 1.5)
        elseif sm then
            y = y + math.floor(4 * s)
            out[#out + 1] = draw_text(px + pad, y, sfs, tag(C.dim, "nothing marked — Enter takes the whole line"))
            y = y + math.floor(sfs * 1.5)
        end
    elseif state == "typing" then
        out[#out + 1] = draw_text(px + pad, y, fs, tag(C.dim, "Saving  ") .. "{\\b1}" .. tag(C.accent, ass_escape(chosen_word)) .. "{\\b0}")
        y = y + line_h
    end

    -- Media status line
    y = y + math.floor(6 * s)
    local media
    if snapshot and snapshot.a_src then
        media = string.format("screenshot  •  audio clip %.1fs", snapshot.a_end - snapshot.a_start)
    else
        media = "screenshot  •  " .. tag(C.err, "no audio source")
    end
    out[#out + 1] = draw_text(px + pad, y, sfs, tag(C.faint, media))
    y = y + math.floor(sfs * 1.8)

    -- Footer hints
    local function key(k) return "{\\b1}" .. tag(C.text, k) .. "{\\b0}" end
    local hints = {
        selecting    = key("↑↓") .. " move   " .. key("Tab") .. " mark   " .. key("Enter") .. " next   " .. key("Esc") .. " close",
        editing_word = key("Enter") .. " next   " .. key("Ctrl+U") .. " clear   " .. key("Esc") .. " back",
        typing       = key("Enter") .. " save   " .. key("Esc") .. " back",
    }
    out[#out + 1] = draw_text(px + pad, y, sfs, tag(C.dim, hints[state]))
    y = y + math.floor(sfs * 1.4) + pad

    -- Panel background goes first
    table.insert(out, 1, draw_box(px, py, pw, y - py, math.floor(14 * s), C.panel, C.panel_a))

    overlay.data = table.concat(out, "\n")
    overlay:update()
end

-- ============================================================
-- SAVE
-- ============================================================

-- ------------------------------------------------------------ Anki queue (german-anki add-row)

-- remove the first line equal to `row` from `file`; append it to `done_file` if to_done
local function move_row_to_done(row, to_done, file, done_file)
    file, done_file = file or MARKDOWN_FILE, done_file or DONE_FILE
    local f = io.open(file, "r")
    if not f then return false, "cannot read " .. file end
    local lines, removed = {}, false
    for line in f:lines() do
        if not removed and line == row then removed = true else lines[#lines + 1] = line end
    end
    f:close()
    if not removed then return false, "row not found in to-do file" end

    if to_done then
        local done_new = not file_exists(done_file)
        local d, err = io.open(done_file, "a")
        if not d then return false, tostring(err) end
        if done_new then
            d:write("| German | English | Image | Audio |\n| ------ | ------- | ----- | ----- |\n")
        end
        d:write(row .. "\n")
        d:close()
    end

    local w, err2 = io.open(file, "w")
    if not w then return false, tostring(err2) end
    w:write(table.concat(lines, "\n") .. "\n")
    w:close()
    return true
end

local anki_queue, anki_busy = {}, false

local function run_anki_queue()
    if anki_busy or #anki_queue == 0 then return end
    anki_busy = true
    local job = table.remove(anki_queue, 1)
    mp.command_native_async({
        name = "subprocess", args = { GERMAN_ANKI, job.cmd, job.row },
        playback_only = false, capture_stdout = true, capture_stderr = true,
    }, function(success, res)
        local out = res and res.stdout or ""
        local result = trim(out:match("RESULT: ([^\n]*)") or "")
        if success and res.status == 0 then
            -- only brand-new cards are logged in the done file; words that were already in
            -- Anki (media filled in, or already complete) just leave the to-do file
            local kind = trim(out:match("STATUS: ([^\n]*)") or "added")
            local moved, err = move_row_to_done(job.row, kind == "added", job.file, job.done_file)
            if moved then
                show_toast({ tag(C.ok, "Anki ✓ ") .. ass_escape(result) }, 3)
            else
                show_toast({ tag(C.ok, "Anki ✓ ") .. ass_escape(result),
                             tag(C.err, "row not moved to done file: " .. ass_escape(err)) }, 6)
            end
            mp.commandv("script-message", "german-subs-refresh")
        else
            local why = result ~= "" and result
                or trim(res and (res.stderr ~= "" and res.stderr or res.error_string) or "?"):sub(-200)
            msg.error("german-anki " .. job.cmd .. " failed for " .. job.word .. ": " .. tostring(res and res.stderr))
            show_toast({ tag(C.err, "Anki ✗ ") .. "{\\b1}" .. ass_escape(job.word) .. "{\\b0}",
                         tag(C.dim, ass_escape(why)), tag(C.faint, "row kept in the to-do file") }, 7)
        end
        anki_busy = false
        run_anki_queue()
    end)
end

local function queue_for_anki(row, word, sentence_mode)
    if not GERMAN_ANKI then return end
    anki_queue[#anki_queue + 1] = sentence_mode
        and { row = row, word = word, cmd = "add-sentence", file = SENTENCE_FILE, done_file = SENTENCE_DONE_FILE }
        or { row = row, word = word, cmd = "add-row", file = MARKDOWN_FILE, done_file = DONE_FILE }
    run_anki_queue()
end

-- ------------------------------------------------------------ screenshot

local function resume_playback()
    if not was_paused then mp.set_property_native("pause", false) end
end

-- german-subs hides mpv's own subtitle and draws a coloured copy on the OSD, which a
-- "subtitles" screenshot does not include. Ask it to hand the real subtitle back for the
-- shot (it answers german-subs-suspended); without german-subs we shoot after a timeout.
local function screenshot_with_subs(path, on_done)
    local done = false
    local function shoot()
        if done then return end
        done = true
        mp.unregister_script_message("german-subs-suspended")
        local ok = pcall(function()
            mp.command_native({ "screenshot-to-file", path, "subtitles" })
        end) and file_exists(path)
        mp.commandv("script-message", "german-subs-resume")
        on_done(ok)
    end
    mp.register_script_message("german-subs-suspended", shoot)
    mp.commandv("script-message", "german-subs-suspend")
    mp.add_timeout(0.5, shoot)
end

-- Called with the video still paused; playback resumes once the screenshot is taken.
local function execute_save(meaning)
    local german = trim(chosen_word)
    meaning = trim(meaning)
    if german == "" then show_toast({ tag(C.err, "✗ Empty word — nothing saved") }); resume_playback(); return end
    local sentence_mode = (mode == "sentence")

    if not ensure_dirs() then
        show_toast({ tag(C.err, "✗ Could not create " .. ass_escape(ASSET_DIR)) }, 6)
        resume_playback()
        return
    end

    local slug_src = german
    if sentence_mode then
        local first = {}
        for w in german:gmatch("%S+") do
            if #first >= SENTENCE_SLUG_WORDS then break end
            first[#first + 1] = w
        end
        slug_src = table.concat(first, " ")
    end
    local name = next_versioned_name(slug_src)
    local image_name = name .. ".webp"
    local audio_name = name .. ".ogg"
    local image_path = ASSET_DIR .. "/" .. image_name
    local audio_path = ASSET_DIR .. "/" .. audio_name

    local snap = snapshot
    screenshot_with_subs(image_path, function(shot_ok)
    resume_playback()

    local function finish(audio_ok, audio_err)
        local image_ref = ""
        if shot_ok then
            image_ref = (IMAGE_SIZE ~= "") and string.format("![[%s\\|%s]]", image_name, IMAGE_SIZE)
                or string.format("![[%s]]", image_name)
        end
        local audio_ref = audio_ok and string.format("![[%s]]", audio_name) or ""
        local row = string.format("| %s | %s | %s | %s |\n",
            markdown_cell(german), markdown_cell(meaning), image_ref, audio_ref)
        local ok, err = append_markdown(row, sentence_mode and SENTENCE_FILE or MARKDOWN_FILE)
        if not ok then
            show_toast({ tag(C.err, "✗ Could not write markdown"), tag(C.dim, ass_escape(err)) }, 7)
            return
        end
        queue_for_anki((row:gsub("\n$", "")), german, sentence_mode)
        show_toast({
            tag(C.ok, "✓ ") .. "{\\b1}" .. ass_escape(german) .. "{\\b0}",
            (shot_ok and tag(C.ok, "screenshot ✓") or tag(C.err, "screenshot ✗")) .. tag(C.faint, "  •  ") ..
            (audio_ok and tag(C.ok, "audio ✓") or tag(C.err, "audio ✗ " .. ass_escape(audio_err or ""))),
        }, audio_ok and shot_ok and 2.5 or 5)
    end

    show_toast({ tag(C.dim, "Saving ") .. "{\\b1}" .. ass_escape(german) .. "{\\b0}" .. tag(C.dim, " — cutting audio…") }, 30)
    extract_audio(snap, audio_path, finish)
    end)
end

-- ============================================================
-- INPUT HANDLING
-- ============================================================

local function set_input(str)
    input_chars = utf8_chars(str or "")
    cursor_pos = #input_chars + 1
end

local function close_ui(keep_paused)
    state = "inactive"
    for _, name in ipairs(active_bindings) do mp.remove_key_binding(name) end
    active_bindings = {}
    render()
    if not keep_paused and not was_paused then mp.set_property_native("pause", false) end
end

local function on_input_changed()
    if state == "selecting" then update_filter() end
    render()
end

local function insert_text(text)
    for _, ch in ipairs(utf8_chars(text)) do
        table.insert(input_chars, cursor_pos, ch)
        cursor_pos = cursor_pos + 1
    end
    on_input_changed()
end

local function backspace()
    if cursor_pos > 1 then
        table.remove(input_chars, cursor_pos - 1)
        cursor_pos = cursor_pos - 1
        on_input_changed()
    end
end

local function delete_word_back()
    while cursor_pos > 1 and input_chars[cursor_pos - 1] == " " do
        table.remove(input_chars, cursor_pos - 1); cursor_pos = cursor_pos - 1
    end
    while cursor_pos > 1 and input_chars[cursor_pos - 1] ~= " " do
        table.remove(input_chars, cursor_pos - 1); cursor_pos = cursor_pos - 1
    end
    on_input_changed()
end

local function delete_fwd()
    if cursor_pos <= #input_chars then
        table.remove(input_chars, cursor_pos)
        on_input_changed()
    end
end

local function move_cursor(d)
    cursor_pos = math.max(1, math.min(#input_chars + 1, cursor_pos + d))
    render()
end

local function move_selection(d)
    if state ~= "selecting" or #filtered == 0 then return end
    sel = sel + d
    if sel < 1 then sel = #filtered end
    if sel > #filtered then sel = 1 end
    render()
end

local function toggle_mark()
    if state ~= "selecting" then return end
    local ti = current_token()
    if not ti then return end
    marked[ti] = not marked[ti] or nil
    if marked[ti] then
        mark_order[#mark_order + 1] = ti
    else
        for k, i in ipairs(mark_order) do if i == ti then table.remove(mark_order, k); break end end
    end
    if #input_chars > 0 then
        -- clear the search so the next word can be typed right away
        set_input("")
        update_filter()
        for k, i in ipairs(filtered) do if i == ti then sel = k end end
    end
    move_selection(1)
end

local function selected_phrase()
    if mode == "sentence" then
        if #mark_order == 0 then
            -- nothing marked: the whole subtitle line, without a leading dialogue dash
            return trim(sentence:gsub("%s*\n%s*", " "):gsub("^[-–—]%s*", ""))
        end
        local words = {}
        for _, i in ipairs(mark_order) do words[#words + 1] = tokens[i].word end
        return table.concat(words, " ")
    end
    local idx = {}
    for i in pairs(marked) do idx[#idx + 1] = i end
    if #idx == 0 then
        local ti = current_token()
        return ti and tokens[ti].word or nil
    end
    table.sort(idx)
    local words = {}
    for _, i in ipairs(idx) do words[#words + 1] = tokens[i].word end
    return table.concat(words, " ")
end

local function handle_enter()
    if state == "selecting" then
        local phrase = selected_phrase()
        if not phrase then return end
        chosen_word = phrase
        set_input(chosen_word)
        state = "editing_word"
    elseif state == "editing_word" then
        local w = trim(table.concat(input_chars))
        if w == "" then return end
        chosen_word = w
        set_input("")
        state = "typing"
    elseif state == "typing" then
        local meaning = table.concat(input_chars)
        if mode == "sentence" and trim(meaning) == "" then
            show_toast({ tag(C.err, "Type the English translation first") }, 2)
            return
        end
        close_ui(true)   -- execute_save resumes playback once the screenshot is taken
        execute_save(meaning)
        return
    end
    render()
end

local function handle_back()
    if state == "selecting" then
        close_ui()
    elseif state == "editing_word" then
        set_input("")
        state = "selecting"
        update_filter()
        render()
    elseif state == "typing" then
        set_input(chosen_word)
        state = "editing_word"
        render()
    end
end

local function bind_keys()
    local function add(key, name, fn, opts)
        name = "gwc-" .. name
        active_bindings[#active_bindings + 1] = name
        mp.add_forced_key_binding(key, name, fn, opts)
    end
    local rep = { repeatable = true }

    add("UP", "up", function() move_selection(-1) end, rep)
    add("DOWN", "down", function() move_selection(1) end, rep)
    add("LEFT", "left", function() move_cursor(-1) end, rep)
    add("RIGHT", "right", function() move_cursor(1) end, rep)
    add("HOME", "home", function() cursor_pos = 1; render() end)
    add("END", "end", function() cursor_pos = #input_chars + 1; render() end)
    add("TAB", "tab", toggle_mark)
    add("ENTER", "enter", handle_enter)
    add("KP_ENTER", "kpenter", handle_enter)
    add("ESC", "esc", handle_back)
    add("BS", "bs", backspace, rep)
    add("ctrl+BS", "ctrlbs", delete_word_back, rep)
    add("DEL", "del", delete_fwd, rep)
    add("ctrl+u", "ctrlu", function() set_input(""); on_input_changed() end)
    add("SPACE", "space", function() insert_text(" ") end, rep)

    -- Any printable character (äöüß, capitals, punctuation...)
    add("ANY_UNICODE", "unicode", function(t)
        if t.event == "up" then return end
        if t.key_text then insert_text(t.key_text) end
    end, { repeatable = true, complex = true })

    -- Swallow mouse/seek keys so the video doesn't move while typing
    local block = { "PGUP", "PGDWN", "WHEEL_UP", "WHEEL_DOWN", "WHEEL_LEFT", "WHEEL_RIGHT",
        "MBTN_LEFT", "MBTN_RIGHT", "MBTN_LEFT_DBL", "MBTN_MID", "MBTN_BACK", "MBTN_FORWARD",
        "shift+LEFT", "shift+RIGHT", "shift+UP", "shift+DOWN", "ctrl+LEFT", "ctrl+RIGHT" }
    for i, k in ipairs(block) do add(k, "block-" .. i, function() end) end
end

-- ============================================================
-- LAUNCHER
-- ============================================================

local function start_capture(m)
    if state ~= "inactive" then close_ui(); return end
    mode = m or "word"

    local text = mp.get_property("sub-text")
    if not text or trim(text) == "" then
        show_toast({ tag(C.err, "No subtitle on screen") }, 2)
        return
    end

    tokens = {}
    for raw in text:gmatch("%S+") do
        local w = clean_word(raw)
        if w ~= "" then tokens[#tokens + 1] = { raw = raw, word = w } end
    end
    if #tokens == 0 then
        show_toast({ tag(C.err, "No words found in subtitle") }, 2)
        return
    end
    sentence = text
    marked = {}
    mark_order = {}
    chosen_word = ""
    set_input("")
    update_filter()

    was_paused = mp.get_property_native("pause")
    mp.set_property_native("pause", true)
    snapshot = take_snapshot()

    state = "selecting"
    bind_keys()
    render()
end

mp.add_forced_key_binding(HOTKEY, "german-word-capture", function() start_capture("word") end)
mp.register_script_message("german-word-capture", function() start_capture("word") end)
if SENTENCE_HOTKEY ~= "" then
    mp.add_forced_key_binding(SENTENCE_HOTKEY, "german-sentence-capture", function() start_capture("sentence") end)
end
mp.register_script_message("german-sentence-capture", function() start_capture("sentence") end)
mp.observe_property("osd-dimensions", "native", function() if state ~= "inactive" then render() end end)
