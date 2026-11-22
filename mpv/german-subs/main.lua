-- german-subs: colour German subtitles by their status in Anki.
--
--   white  : in Anki, with screenshot and audio
--   blue   : in Anki, has the screenshot but no audio
--   yellow : in Anki, missing the screenshot (and the audio)
--   red    : a real German word (in the dictionary) that is not in Anki
--   gray   : not in the dictionary (names, typos, made-up words)
--   plain  : ignored (ignore.txt), numbers, punctuation  (white by default)
--
-- Colours depend on Anki only. `german-anki sync` exports the deck from AnkiConnect (in
-- the background) to <data>/anki-words.json on every file load, every SUBS_POLL_SECONDS
-- while a video is open (cheap: it only reloads when the deck changed), and whenever
-- another script sends "script-message german-subs-refresh" (german-word-capture does after
-- a word has been put into Anki). If Anki is not running the previous export is used.
-- Inflected forms (ging -> gehen, Häuser -> Haus) come from the reader.dict
-- dictionary; split separable verbs ("hört ... auf") are joined back together.
--
-- mpv's own rendering of the primary subtitle is hidden and the line is redrawn
-- as an OSD overlay, so subtitle styling/positioning (ASS signs etc.) is lost.
-- Image subtitles (PGS/VobSub) have no text: they are left untouched.
--
-- Keys:  alt+c toggle colouring   (v still hides/shows subtitles)
-- Settings: SUBS_* in ~/.config/german-anki/.env
-- Ignore list: ignore.txt next to this script (function words + common names)

local mp      = require("mp")
local msg     = require("mp.msg")
local utils   = require("mp.utils")

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

local DATA_DIR = conf.str("GERMAN_ANKI_DATA_DIR", "~/.local/share/german-anki")
local opts = {
    german_anki   = conf.str("GERMAN_ANKI_BIN", "~/.local/bin/german-anki"),
    toggle_key    = conf.str("SUBS_TOGGLE_KEY", "alt+c"),
    enabled       = conf.bool("SUBS_ENABLED", true),
    -- ASS colours are BGR hex
    color_ok      = conf.str("SUBS_COLOR_OK", "FFFFFF"),
    color_noaudio = conf.str("SUBS_COLOR_NOAUDIO", "FFC866"),   -- light blue
    color_missing = conf.str("SUBS_COLOR_MISSING", "00E5FF"),   -- yellow
    color_unknown = conf.str("SUBS_COLOR_UNKNOWN", "5A5AFF"),   -- red
    color_nodict  = conf.str("SUBS_COLOR_NODICT", "B4B4B4"),    -- gray
    color_plain   = conf.str("SUBS_COLOR_PLAIN", "FFFFFF"),
    -- box behind the subtitle
    background    = conf.bool("SUBS_BACKGROUND", true),
    bg_color      = conf.str("SUBS_BG_COLOR", "000000"),
    bg_alpha      = conf.str("SUBS_BG_ALPHA", "00"),        -- 00 = opaque ... FF = invisible
    bg_padding    = conf.num("SUBS_BG_PADDING", 10),
    -- seconds between Anki checks while a video is open (0 = only on load / after a capture)
    poll_seconds  = conf.num("SUBS_POLL_SECONDS", 10),
}

local SCRIPT_DIR  = mp.get_script_directory()
local JSON_PATH   = DATA_DIR .. "/anki-words.json"
local WORDLIST    = DATA_DIR .. "/dict/wordlist.txt"
-- words never coloured (function words, common names)
local IGNORE_FILES = { SCRIPT_DIR .. "/ignore.txt" }

-- separable verb prefixes that can end up at the end of a clause
local SEP_PREFIXES = {}
for p in ([[ab an auf aus bei ein fest fort her hin los mit nach vor weg weiter zu zurück
            zusammen vorbei heraus herein hinaus hinein herum herunter hinunter hoch
            durch um über unter wieder teil statt kennen frei fern dabei dazu]]):gmatch("%S+") do
    SEP_PREFIXES[p] = true
end

-- ============================================================ state

local overlay   = mp.create_osd_overlay("ass-events")
local forms     = {}      -- lowercase form -> "ok" | "noaudio" | "missing"
local ignore    = {}
local generated = nil     -- timestamp of the loaded export
local enabled   = opts.enabled
local user_wants_subs = mp.get_property_native("sub-visibility")
local we_hid    = false   -- we set sub-visibility=no ourselves
local suspended = false   -- another script (word capture) needs mpv's own subtitle for a screenshot
local dict_fh, dict_size
local dict_cache = {}

-- ============================================================ helpers

local function trim(s) return (tostring(s or ""):gsub("^%s+", ""):gsub("%s+$", "")) end

local function ass_escape(s)
    return (tostring(s or ""):gsub("\\", "\\\239\187\191"):gsub("{", "\\{"):gsub("}", "\\}"))
end

local function lower(s)
    return (tostring(s):lower():gsub("Ä", "ä"):gsub("Ö", "ö"):gsub("Ü", "ü"):gsub("ẞ", "ß"))
end

local function read_file(path)
    local f = io.open(path, "rb")
    if not f then return nil end
    local s = f:read("*a")
    f:close()
    return s
end

local function load_ignore()
    ignore = {}
    for _, path in ipairs(IGNORE_FILES) do
        local s = read_file(path) or ""
        for line in s:gmatch("[^\r\n]+") do
            line = trim(line)
            if line ~= "" and line:sub(1, 1) ~= "#" then ignore[lower(line)] = true end
        end
    end
end

local function load_json()
    local s = read_file(JSON_PATH)
    if not s then return false end
    local data = utils.parse_json(s)
    if type(data) ~= "table" or type(data.forms) ~= "table" then
        msg.warn("could not parse " .. JSON_PATH)
        return false
    end
    forms, generated = data.forms, data.generated
    return true
end

-- ------------------------------------------------ dictionary (binary search in a sorted file)

local function open_dict()
    if dict_fh then return true end
    dict_fh = io.open(WORDLIST, "rb")
    if not dict_fh then return false end
    dict_size = dict_fh:seek("end")
    return true
end

local function byte_less(a, b)   -- plain byte order, independent of the C locale
    local n = math.min(#a, #b)
    for i = 1, n do
        local x, y = a:byte(i), b:byte(i)
        if x ~= y then return x < y end
    end
    return #a < #b
end

-- first complete line at or after byte offset `pos`
local function line_after(pos)
    dict_fh:seek("set", pos)
    if pos > 0 then dict_fh:read("*l") end
    return dict_fh:read("*l")
end

local function in_dictionary(w)
    local c = dict_cache[w]
    if c ~= nil then return c end
    if not open_dict() then return false end
    local lo, hi = 0, dict_size
    while lo < hi do
        local mid = math.floor((lo + hi) / 2)
        local line = line_after(mid)
        if line == nil or not byte_less(line, w) then hi = mid else lo = mid + 1 end
    end
    local found = line_after(lo) == w
    dict_cache[w] = found
    return found
end

-- ============================================================ classification

-- strip leading/trailing punctuation (ASCII and common typographic quotes/dashes)
local MB_PUNCT = { "„", "“", "”", "‚", "‘", "’", "«", "»", "…", "–", "—", "‹", "›" }

local function strip_edge(s, at_start)
    while s ~= "" do
        local changed = false
        if at_start then
            if s:match("^%p") then s = s:sub(2); changed = true end
        else
            if s:match("%p$") then s = s:sub(1, -2); changed = true end
        end
        if not changed then
            for _, p in ipairs(MB_PUNCT) do
                if at_start and s:sub(1, #p) == p then s = s:sub(#p + 1); changed = true; break end
                if not at_start and #s >= #p and s:sub(-#p) == p then s = s:sub(1, -#p - 1); changed = true; break end
            end
        end
        if not changed then break end
    end
    return s
end

local function split_token(tok)
    local core = strip_edge(strip_edge(tok, true), false)
    if core == "" then return tok, "", "" end
    local s = tok:find(core, 1, true)
    return tok:sub(1, s - 1), core, tok:sub(s + #core)
end

local function lookup_key(core)
    local w = lower(core)
    w = w:gsub("'s$", ""):gsub("’s$", "")   -- geht's, gibt’s
    return w
end

local CLAUSE_PUNCT = { ",", ".", ";", ":", "!", "?", "…", "–", "—", "\"", "“", "”", "„" }
local CONJUNCTIONS = {}
for w in ("und oder aber denn sondern weil dass wenn als ob obwohl während bevor nachdem damit"):gmatch("%S+") do
    CONJUNCTIONS[w] = true
end

local function has_clause_punct(s)
    for _, p in ipairs(CLAUSE_PUNCT) do
        if s:find(p, 1, true) then return true end
    end
    return false
end

-- pieces: list of { text=, ws=true } or { lead=, core=, trail=, key=, clause=, status= }
-- Works on the whole subtitle (all lines) so a verb and its prefix can sit on different lines.
local function classify_text(text)
    local pieces, words = {}, {}
    local pos, clause = 1, 1
    while pos <= #text do
        local ws = text:match("^%s+", pos)
        if ws then
            pieces[#pieces + 1] = { text = ws, ws = true }
            pos = pos + #ws
        else
            local tok = text:match("^%S+", pos)
            pos = pos + #tok
            local lead, core, trail = split_token(tok)
            local key = lookup_key(core)
            if has_clause_punct(lead) or CONJUNCTIONS[key] then clause = clause + 1 end
            local p = { lead = lead, core = core, trail = trail, key = key, clause = clause }
            pieces[#pieces + 1] = p
            if core ~= "" then words[#words + 1] = p end
            if has_clause_punct(trail) then clause = clause + 1 end
        end
    end

    -- pass 1: ignore list, numbers, direct hits
    for _, p in ipairs(words) do
        local k = p.key
        if k == "" or not k:find("%a") and not k:find("[\128-\255]") then
            p.status = "plain"
        elseif ignore[k] then
            p.status = "plain"
        elseif forms[k] then
            p.status = forms[k]
        end
    end

    -- pass 2: split separable verbs. A prefix that closes its clause ("Tom hört nicht auf")
    -- is joined with the earlier words of that clause; the first combination that is a deck
    -- form ("aufhört" -> aufhören) wins and overrides the verb's own match (hören).
    for j, q in ipairs(words) do
        local closes_clause = (j == #words) or words[j + 1].clause ~= q.clause
        if SEP_PREFIXES[q.key] and closes_clause then
            for i = 1, j - 1 do
                local p = words[i]
                if p.clause == q.clause then
                    local joined = forms[q.key .. p.key]
                    if joined then
                        p.status, q.status = joined, joined
                        break
                    end
                end
            end
        end
    end

    -- pass 3: whatever is left is red if it is a real word, gray otherwise (names, typos)
    for _, p in ipairs(words) do
        if not p.status then
            p.status = in_dictionary(p.key) and "unknown" or "nodict"
        end
    end
    return pieces
end

-- ============================================================ rendering

local COLORS = {
    ok = opts.color_ok, noaudio = opts.color_noaudio, missing = opts.color_missing,
    unknown = opts.color_unknown, nodict = opts.color_nodict, plain = opts.color_plain,
}

local function is_image_sub()
    local t = mp.get_property_native("current-tracks/sub")
    if not t then return false end
    if t.image ~= nil then return t.image end
    local c = t.codec or ""
    return c:find("pgs") or c:find("dvd_sub") or c:find("dvb_sub") or c:find("xsub")
end

-- hide mpv's own primary subtitle while we draw it; give it back otherwise
local function update_native_visibility()
    local want_hidden = enabled and not suspended and user_wants_subs and not is_image_sub()
    local cur = mp.get_property_native("sub-visibility")
    if want_hidden and cur then
        we_hid = true
        mp.set_property_native("sub-visibility", false)
    elseif not want_hidden and not cur and (we_hid or user_wants_subs) then
        we_hid = false
        mp.set_property_native("sub-visibility", user_wants_subs)
    end
end

local function num(prop, default)
    return mp.get_property_number(prop) or default
end

local function render()
    if not enabled or suspended or not user_wants_subs or is_image_sub() then overlay:remove(); return end
    local text = mp.get_property("sub-text") or ""
    if trim(text) == "" then overlay:remove(); return end

    local dims = mp.get_property_native("osd-dimensions") or {}
    local w, h = dims.w or 0, dims.h or 0
    if w <= 0 or h <= 0 then w, h = 16, 9 end

    local res_y = 720
    local res_x = math.floor(res_y * w / h)
    local scale = num("sub-scale", 1)
    local fs = num("sub-font-size", 38) * scale
    local bord = (mp.get_property_number("sub-outline-size") or num("sub-border-size", 3)) * scale
    local shad = num("sub-shadow-offset", 0) * scale
    local margin = num("sub-margin-y", 22)
    local y = res_y * num("sub-pos", 100) / 100 - margin
    local font = mp.get_property("sub-font", "sans-serif")

    local out, dbg = {}, {}
    for _, p in ipairs(classify_text(trim(text))) do
        if p.ws then
            out[#out + 1] = p.text:find("\n") and "\\N" or ass_escape(p.text)
        else
            if p.core ~= "" then dbg[#dbg + 1] = p.core .. "=" .. tostring(p.status) end
            out[#out + 1] = string.format("{\\c&H%s&}%s{\\c&H%s&}%s{\\c&H%s&}%s",
                COLORS.plain, ass_escape(p.lead),
                COLORS[p.status or "plain"], ass_escape(p.core),
                COLORS.plain, ass_escape(p.trail))
        end
    end
    msg.debug(table.concat(dbg, " "))

    overlay.res_x, overlay.res_y = res_x, res_y
    local text_event = string.format(
        "{\\an2\\pos(%d,%d)\\fn%s\\fs%.1f\\bord%.2f\\shad%.2f\\3c&H000000&\\4c&H000000&\\q0}%s",
        math.floor(res_x / 2), math.floor(y - opts.bg_padding / 2), font, fs, bord, shad, table.concat(out))

    -- background box: measure the text off-screen, then draw a rectangle behind it
    local box = ""
    if opts.background then
        overlay.data, overlay.hidden, overlay.compute_bounds = text_event, true, true
        local b = overlay:update()
        overlay.hidden, overlay.compute_bounds = false, false
        if b and b.x0 and b.x1 and b.x1 > b.x0 then
            local px, py = opts.bg_padding, opts.bg_padding / 2
            local x0, y0 = math.floor(b.x0 - px), math.floor(b.y0 - py)
            local x1, y1 = math.ceil(b.x1 + px), math.ceil(b.y1 + py)
            box = string.format(
                "{\\an7\\pos(0,0)\\bord0\\shad0\\1c&H%s&\\1a&H%s&\\p1}m %d %d l %d %d l %d %d l %d %d{\\p0}\n",
                opts.bg_color, opts.bg_alpha, x0, y0, x1, y0, x1, y1, x0, y1)
        end
    end
    overlay.data = box .. text_event
    overlay:update()
end

-- ============================================================ sync

local sync_running, sync_again = false, false
local anki_offline = false

-- announce=true on file load: tell the user (once per file) when Anki is offline
local function run_sync(announce)
    if sync_running then sync_again = true; return end
    sync_running = true
    mp.command_native_async({
        name = "subprocess",
        args = { opts.german_anki, "sync" },
        playback_only = false, capture_stdout = true, capture_stderr = true,
    }, function(success, res)
        local status = success and res and res.status or -1
        local out = trim(res and res.stdout or "")
        if status == 0 and out:find("^unchanged") then
            msg.debug("sync: " .. out)          -- nothing to reload
        elseif status == 0 then
            load_json()
            dict_cache = {}
            if not dict_fh then open_dict() end
            msg.info("sync: " .. out)
            render()
        end
        if status ~= 0 then
            local err = trim(res and (res.stderr ~= "" and res.stderr or res.error_string) or "?")
            if not anki_offline or announce then msg.warn("sync: " .. err) end
            if announce then
                mp.osd_message(status == 3
                    and "german-subs: Anki not reachable — using the last Anki export"
                    or "german-subs: sync failed — " .. err, 4)
            end
        end
        anki_offline = status ~= 0
        sync_running = false
        if sync_again then sync_again = false; run_sync(false) end
    end)
end

-- ============================================================ wiring

mp.observe_property("sub-visibility", "bool", function(_, v)
    if v == nil then return end
    if we_hid and v == true and enabled and not is_image_sub() then
        -- the user pressed `v` while we were drawing: that means "hide subtitles"
        user_wants_subs = not user_wants_subs
        mp.set_property_native("sub-visibility", false)
    elseif not we_hid or not enabled then
        user_wants_subs = v
    end
    update_native_visibility()
    render()
end)

mp.observe_property("current-tracks/sub", "native", function()
    update_native_visibility(); render()
end)
for _, p in ipairs({ "sub-text", "osd-dimensions", "sub-scale", "sub-font-size", "sub-pos" }) do
    mp.observe_property(p, "native", render)
end

mp.register_event("file-loaded", function() load_ignore(); run_sync(true) end)

-- re-read Anki on request (german-word-capture sends this once words.py has added a card)
mp.register_script_message("german-subs-refresh", function() run_sync(false) end)

-- poll Anki while a file is open, so cards added any other way (clipboard words.py, the
-- Anki editor, add-ons) recolour within a few seconds. An unchanged poll costs ~0.3s of
-- background work and reloads nothing.
if opts.poll_seconds > 0 then
    mp.add_periodic_timer(opts.poll_seconds, function()
        if mp.get_property("path") then run_sync(false) end
    end)
end

-- Screenshot handshake (used by german-word-capture.lua):
--   script-message german-subs-suspend  -> mpv's own subtitle is shown again, then we
--                                          broadcast german-subs-suspended
--   script-message german-subs-resume   -> back to the coloured overlay
mp.register_script_message("german-subs-suspend", function()
    suspended = true
    update_native_visibility()
    render()
    mp.commandv("script-message", "german-subs-suspended")
end)
mp.register_script_message("german-subs-resume", function()
    suspended = false
    update_native_visibility()
    render()
end)

mp.add_key_binding(opts.toggle_key, "toggle", function()
    enabled = not enabled
    update_native_visibility()
    render()
    mp.osd_message("German subtitle colours: " .. (enabled and "on" or "off"), 1.5)
end)

mp.register_event("shutdown", function()
    if we_hid then mp.set_property_native("sub-visibility", user_wants_subs) end
end)

load_ignore()
load_json()
open_dict()
