#!/usr/bin/env python3
"""
scarlett18i8 - control a 1st-gen Focusrite Scarlett 18i8 (USB 1235:8014) on
modern macOS, where Scarlett MixControl can no longer find the device.

Talks to the unit with plain USB class control requests via libusb (no driver,
no kext). Protocol from the Linux kernel's sound/usb/mixer_scarlett.c
(reverse-engineered from MixControl by T. Hoffmann, R. Gareus, C. Arges).

Usage:  scarlett18i8.py help

SPDX-License-Identifier: GPL-2.0-or-later
"""
import ctypes, ctypes.util, json, math, os, sys, time

VID, PID = 0x1235, 0x8014
CS_CUR, CS_MEM = 0x01, 0x03
RT_IN, RT_OUT = 0xA1, 0x21  # class | interface recipient

# ---------------------------------------------------------------- libusb ---
def _load_libusb():
    for p in ("/opt/homebrew/lib/libusb-1.0.dylib", "/usr/local/lib/libusb-1.0.dylib",
              ctypes.util.find_library("usb-1.0") or ""):
        if p and os.path.exists(p):
            return ctypes.CDLL(p)
    sys.exit("libusb not found - install it with: brew install libusb")

lib = _load_libusb()
lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
lib.libusb_open_device_with_vid_pid.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16]
lib.libusb_control_transfer.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8,
                                        ctypes.c_uint16, ctypes.c_uint16, ctypes.c_char_p,
                                        ctypes.c_uint16, ctypes.c_uint]
lib.libusb_close.argtypes = [ctypes.c_void_p]
lib.libusb_error_name.restype = ctypes.c_char_p


class Scarlett:
    def __init__(self):
        self.ctx = ctypes.c_void_p()
        if lib.libusb_init(ctypes.byref(self.ctx)) != 0:
            sys.exit("libusb_init failed")
        self.h = lib.libusb_open_device_with_vid_pid(self.ctx, VID, PID)
        if not self.h:
            sys.exit("Scarlett 18i8 (1st gen, 1235:8014) not found on USB")

    def _xfer(self, rt, req, unit, wvalue, data_or_len):
        if isinstance(data_or_len, int):
            buf = ctypes.create_string_buffer(data_or_len); n = data_or_len
        else:
            buf = ctypes.create_string_buffer(bytes(data_or_len), len(data_or_len)); n = len(data_or_len)
        r = lib.libusb_control_transfer(self.h, rt, req, wvalue & 0xFFFF, (unit << 8) | 0, buf, n, 1000)
        if r < 0:
            raise IOError(f"USB request unit={unit:#04x} wValue={wvalue:#06x} failed: "
                          f"{lib.libusb_error_name(r).decode()}")
        return buf.raw[:r]

    def get16(self, unit, wvalue):
        return int.from_bytes(self._xfer(RT_IN, CS_CUR, unit, wvalue, 2), "little", signed=True)

    def set16(self, unit, wvalue, v):
        self._xfer(RT_OUT, CS_CUR, unit, wvalue, (v & 0xFFFF).to_bytes(2, "little"))

    def get8(self, unit, wvalue):
        return self._xfer(RT_IN, CS_CUR, unit, wvalue, 1)[0]

    def set8(self, unit, wvalue, v):
        self._xfer(RT_OUT, CS_CUR, unit, wvalue, bytes([v & 0xFF]))

    def mem_read(self, wvalue, n):
        return self._xfer(RT_IN, CS_MEM, 0x3C, wvalue, n)

    def close(self):
        lib.libusb_close(self.h); lib.libusb_exit(self.ctx)


# ----------------------------------------------------------- device model ---
OUT_PAIRS = ["Monitor", "Headphone 1", "Headphone 2", "SPDIF"]   # outputs 1/2,3/4,5/6,7/8
N_MATRIX_IN, N_MIX, N_CAPTURE, N_OUT = 18, 8, 18, 8

# Source numbering (raw value). -1 = Off.
def src_name(v):
    if v < 0:   return "Off"
    if v < 8:   return f"PCM {v + 1}"
    if v < 16:  return f"Analog {v - 7}"
    if v < 18:  return f"SPDIF {v - 15}"
    if v < 26:  return f"ADAT {v - 17}"
    if v < 34:  return f"Mix {chr(ord('A') + v - 26)}"
    return f"?{v}"

def parse_src(s, allow_mix=True):
    t = s.lower().replace(" ", "").replace("-", "")
    if t in ("off", "none", "0"): return -1
    for pre, base, n in (("pcm", 0, 8), ("daw", 0, 8), ("analog", 8, 8), ("an", 8, 8), ("in", 8, 8),
                         ("spdif", 16, 2), ("adat", 18, 8)):
        if t.startswith(pre) and t[len(pre):].isdigit():
            k = int(t[len(pre):])
            if 1 <= k <= n: return base + k - 1
            raise ValueError(f"{s}: channel out of range 1..{n}")
    if allow_mix and t.startswith("mix") and len(t) == 4 and "a" <= t[3] <= "h":
        return 26 + ord(t[3]) - ord("a")
    raise ValueError(f"unknown source '{s}' (try off, pcm1-8, analog1-8, spdif1-2, adat1-8"
                     + (", mixA-H)" if allow_mix else ")"))

def db_str(raw):
    db = raw / 256
    return " -inf" if db <= -128 else f"{db:+5.1f}"

def parse_db(s):
    if s.lower() in ("off", "-inf", "mute"): return -128 * 256
    db = float(s)
    if not -128 <= db <= 6: raise ValueError("gain must be between -128 and +6 dB")
    return int(round(db * 256))

# Unit / control addresses (wValue = control<<8 | index)
U_INPUT, U_MASTER, U_CLOCK, U_RATE = 0x01, 0x0A, 0x28, 0x29
U_MATRIX_MUX, U_OUT_MUX, U_CAP_MUX, U_MATRIX = 0x32, 0x33, 0x34, 0x3C

def w_master_vol(ch): return 0x0200 | ch      # ch 0 = global master, 1..8 = outputs
def w_master_mute(ch): return 0x0100 | ch
def w_out_mux(o): return o                     # o 0..7
def w_cap_mux(i): return i                     # i 0..17
def w_matrix_mux(i): return 0x0600 | i         # i 0..17
def w_matrix_gain(i, m): return (i << 3) | m   # control 0x00
def w_impedance(n): return 0x0900 | n          # n 1..2
def w_pad(n): return 0x0B00 | n                # n 1..4


# ---------------------------------------------------------------- state ---
# The 1st-gen hardware cannot report most of its settings (reads return
# placeholders), so - like MixControl - we keep the authoritative copy here.
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
OFF = -128 * 256

def preset_monitor():
    """Direct-monitoring starting point: all analog inputs (centred) plus DAW
    playback 1/2 into one stereo mix on Monitor, Headphone 1 and Headphone 2."""
    st = {
        "clock_source": 1,
        "outputs": {str(c): {"vol": 0, "mute": 0} for c in range(0, 9)},
        # Monitor/HP1/HP2 L <- Mix A, R <- Mix B; SPDIF off
        "output_route": [26, 27, 26, 27, 26, 27, -1, -1],
        # capture: analog 1-8, SPDIF 1-2, ADAT 1-8
        "capture_route": list(range(8, 16)) + [16, 17] + list(range(18, 26)),
        # mixer rows: analog 1-8, (off x8), PCM 1-2
        "matrix_route": list(range(8, 16)) + [-1] * 8 + [0, 1],
        "matrix_gain": [[OFF] * 8 for _ in range(18)],
        "impedance": {"1": 0, "2": 0},
        "pad": {"1": 0, "2": 0, "3": 0, "4": 0},
    }
    g = st["matrix_gain"]
    for i in range(8):                        # analog inputs, centred (-3 dB pan law)
        g[i][0] = g[i][1] = -3 * 256
    g[16][0] = 0                              # DAW L -> Mix A
    g[17][1] = 0                              # DAW R -> Mix B
    return st

def load_state():
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except FileNotFoundError:
        return None

def save_state(st):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f: json.dump(st, f, indent=1)
    os.replace(tmp, STATE_FILE)

def need_state():
    st = load_state()
    if st is None:
        sys.exit("No saved state yet. Run 'scarlett18i8.py preset monitor' (or 'restore <file>') first,\n"
                 "so the tool knows the full mixer setup - the unit itself can't report it.")
    return st

def write_state(d, st):
    d.set8(U_CLOCK, 0x0100, st["clock_source"])
    for o, v in enumerate(st["output_route"]): d.set16(U_OUT_MUX, w_out_mux(o), v)
    for i, v in enumerate(st["capture_route"]): d.set16(U_CAP_MUX, w_cap_mux(i), v)
    for i, v in enumerate(st["matrix_route"]): d.set16(U_MATRIX_MUX, w_matrix_mux(i), v)
    for i, row in enumerate(st["matrix_gain"]):
        for m, v in enumerate(row): d.set16(U_MATRIX, w_matrix_gain(i, m), v)
    for n, v in st["impedance"].items(): d.set16(U_INPUT, w_impedance(int(n)), v)
    for n, v in st["pad"].items(): d.set16(U_INPUT, w_pad(int(n)), v)
    for c, o in st["outputs"].items():
        d.set16(U_MASTER, w_master_vol(int(c)), o["vol"])
        d.set16(U_MASTER, w_master_mute(int(c)), o["mute"])

CLOCKS = {1: "Internal", 2: "SPDIF", 3: "ADAT"}

def print_state(d, st):
    rate = int.from_bytes(d._xfer(RT_IN, CS_CUR, U_RATE, 0x0100, 4), "little")
    sync = d.mem_read(0x0002, 1)[0]
    print(f"[live]  Rate: {rate} Hz   Sync: {'Locked' if sync else 'NO LOCK'}")
    print(f"[below: settings as last sent by this tool - {STATE_FILE}]\n")
    print(f"Clock: {CLOCKS.get(st['clock_source'], st['clock_source'])}")
    g = st["outputs"]["0"]
    print(f"Master: {db_str(g['vol'])} dB {'MUTED' if g['mute'] else ''}\n")

    print("OUTPUTS            source            vol dB   mute")
    for o in range(N_OUT):
        c = str(o + 1); ov = st["outputs"][c]
        label = f"{OUT_PAIRS[o // 2]} {'LR'[o % 2]}"
        print(f"  {o+1}  {label:<14} {src_name(st['output_route'][o]):<14} {db_str(ov['vol'])}   "
              f"{'MUTED' if ov['mute'] else ''}")

    print("\nINPUTS   1: " + ("Inst/Hi-Z" if st["impedance"]["1"] else "Line") +
          ("  -10dB pad" if st["pad"]["1"] else "") +
          "   2: " + ("Inst/Hi-Z" if st["impedance"]["2"] else "Line") +
          ("  -10dB pad" if st["pad"]["2"] else "") +
          "   3: " + ("-10dB pad" if st["pad"]["3"] else "no pad") +
          "   4: " + ("-10dB pad" if st["pad"]["4"] else "no pad"))

    print("\nMIX MATRIX (gain in dB; blank = off)")
    print("  in  source      " + "".join(f"  Mix{chr(65+m)}" for m in range(N_MIX)))
    for i in range(N_MATRIX_IN):
        row = st["matrix_gain"][i]
        cells = "".join(f"  {'' if v <= OFF else db_str(v):>4}" for v in row)
        print(f"  {i+1:>2}  {src_name(st['matrix_route'][i]):<10}{cells}")

    print("\nCAPTURE (to computer)")
    for i in range(0, N_CAPTURE, 6):
        print("  " + "   ".join(f"{j+1:>2}:{src_name(st['capture_route'][j]):<9}"
                                for j in range(i, min(i + 6, N_CAPTURE))))


# ---------------------------------------------------------------- meters ---
def meters(d):
    names = [src_name(8 + i) for i in range(8)] + ["SPDIF 1", "SPDIF 2"] + [f"ADAT {i}" for i in range(1, 9)]
    try:
        while True:
            raw = d.mem_read(0x0000, 36)
            vals = [int.from_bytes(raw[k:k+2], "little") for k in range(0, len(raw), 2)]
            out = ["\x1b[H\x1b[2J Input meters (Ctrl-C to stop)\n"]
            for n, v in zip(names, vals):
                db = 20 * math.log10(v / 65535) if v > 0 else -99
                bar = "#" * max(0, int((db + 60) / 60 * 40))
                out.append(f" {n:<9} {db:6.1f} dBFS |{bar:<40}|")
            print("\n".join(out), flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print()


# ------------------------------------------------------------------ CLI ---
HELP = """\
scarlett18i8.py - control a 1st-gen Scarlett 18i8 without MixControl

  preset monitor                 starting point: analog 1-8 + DAW 1/2 -> monitors & both phones
  status                         show current settings (as stored by this tool) + live sync
  apply                          re-send all stored settings (e.g. after a power cycle)
  out <1-8> <src>                route a hardware output (1/2 Monitor, 3/4 HP1, 5/6 HP2, 7/8 SPDIF)
  vol <master|1-8> <dB>          output volume, -128..+6 dB ('off' = -inf)
  mute <master|1-8> <on|off>
  matrix <1-18> <src>            choose what feeds mixer input row 1-18 (no mixes)
  mix <1-18> <A-H> <dB|off>      set gain of matrix row into Mix A-H
  capture <1-18> <src>           choose what the computer receives on input channel 1-18
  inst <1|2> <on|off>            instrument (Hi-Z) switch for inputs 1-2
  pad <1-4> <on|off>             -10 dB pad for inputs 1-4
  clock <internal|spdif|adat>
  meters                         live input meters
  backup <file.json>             copy the stored settings to a file
  restore <file.json>            load a settings file into the device
  save                           store current settings in the unit (survives power-off)

  <src>: off, pcm1-8 (DAW playback), analog1-8, spdif1-2, adat1-8, mixA-H

Examples:
  scarlett18i8.py out 1 mixA ; scarlett18i8.py out 2 mixB
  scarlett18i8.py mix 1 A 0 ; scarlett18i8.py mix 1 B 0     # analog 1 in both sides of the monitor mix
"""

def onoff(s):
    if s.lower() in ("on", "1", "yes", "true"): return 1
    if s.lower() in ("off", "0", "no", "false"): return 0
    raise ValueError("expected on/off")

def rng(s, lo, hi, what):
    n = int(s)
    if not lo <= n <= hi: raise ValueError(f"{what} must be {lo}..{hi}")
    return n

def bus(s):
    return 0 if s.lower() == "master" else rng(s, 1, 8, "output")

def main(argv):
    if not argv or argv[0] in ("help", "-h", "--help"):
        print(HELP); return
    cmd, a = argv[0], argv[1:]
    d = Scarlett()
    try:
        if cmd == "meters":
            meters(d); return
        if cmd == "save":
            d._xfer(RT_OUT, CS_MEM, 0x3C, 0x005A, b"\xa5")
            print("settings saved to hardware (they will survive a power cycle)"); return
        if cmd == "preset":
            if a[:1] != ["monitor"]: raise ValueError("available presets: monitor")
            st = preset_monitor(); write_state(d, st); save_state(st)
            print("applied preset 'monitor' (not yet saved to hardware - run 'save' once happy)"); return
        if cmd == "restore":
            with open(a[0]) as f: st = json.load(f)
            write_state(d, st); save_state(st)
            print(f"restored from {a[0]} (run 'save' to make it stick after power-off)"); return

        st = need_state()
        if cmd == "status":
            print_state(d, st); return
        if cmd == "apply":
            write_state(d, st); print("re-sent all settings to the unit"); return
        if cmd == "backup":
            with open(a[0], "w") as f: json.dump(st, f, indent=1)
            print(f"saved to {a[0]}"); return

        if cmd == "out":
            o = rng(a[0], 1, 8, "output") - 1; v = parse_src(a[1])
            d.set16(U_OUT_MUX, w_out_mux(o), v); st["output_route"][o] = v
        elif cmd == "vol":
            c = bus(a[0]); v = parse_db(a[1])
            d.set16(U_MASTER, w_master_vol(c), v); st["outputs"][str(c)]["vol"] = v
        elif cmd == "mute":
            c = bus(a[0]); v = onoff(a[1])
            d.set16(U_MASTER, w_master_mute(c), v); st["outputs"][str(c)]["mute"] = v
        elif cmd == "matrix":
            i = rng(a[0], 1, 18, "matrix row") - 1; v = parse_src(a[1], allow_mix=False)
            d.set16(U_MATRIX_MUX, w_matrix_mux(i), v); st["matrix_route"][i] = v
        elif cmd == "mix":
            i = rng(a[0], 1, 18, "matrix row") - 1; m = a[1].upper()
            if len(m) != 1 or not "A" <= m <= "H": raise ValueError("mix must be A-H")
            v = parse_db(a[2])
            d.set16(U_MATRIX, w_matrix_gain(i, ord(m) - 65), v); st["matrix_gain"][i][ord(m) - 65] = v
        elif cmd == "capture":
            i = rng(a[0], 1, 18, "capture channel") - 1; v = parse_src(a[1])
            d.set16(U_CAP_MUX, w_cap_mux(i), v); st["capture_route"][i] = v
        elif cmd == "inst":
            n = rng(a[0], 1, 2, "input"); v = onoff(a[1])
            d.set16(U_INPUT, w_impedance(n), v); st["impedance"][str(n)] = v
        elif cmd == "pad":
            n = rng(a[0], 1, 4, "input"); v = onoff(a[1])
            d.set16(U_INPUT, w_pad(n), v); st["pad"][str(n)] = v
        elif cmd == "clock":
            v = {"internal": 1, "spdif": 2, "adat": 3}.get(a[0].lower())
            if not v: raise ValueError("clock must be internal, spdif or adat")
            d.set8(U_CLOCK, 0x0100, v); st["clock_source"] = v
        else:
            print(HELP); sys.exit(1)
        save_state(st)
    except (ValueError, IndexError) as e:
        sys.exit(f"error: {e or 'missing argument'}  (see: scarlett18i8.py help)")
    finally:
        d.close()

if __name__ == "__main__":
    main(sys.argv[1:])
