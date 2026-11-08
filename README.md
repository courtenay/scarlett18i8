# scarlett18i8

> [!NOTE]
> **Archived. For most people, use
> [Scarlett MixControl Community Edition](https://github.com/MarecekW/scarlett-mixcontrol-1stgen)
> instead.** It's a full native macOS app (mixer window, presets, menu-bar panel,
> auto-reconnect) and supports every 1st-gen USB Scarlett, with the 18i8 confirmed
> on hardware. This script still works as a small, dependency-light command-line
> alternative, but it won't be developed further.

Control a **1st-generation Focusrite Scarlett 18i8** on modern macOS, where
Focusrite's *Scarlett MixControl* can no longer see the device.

No driver, no kernel extension: a single Python script that talks to the
interface over USB with libusb. It covers everything MixControl did on this unit:
output routing, the 18×8 mix matrix, output volume and mute, Inst/Pad switches,
clock source, input meters and **Save to Hardware**.

## Why MixControl stopped working

Recent macOS (seen on macOS 27) moved class-compliant USB audio out of the
kernel into a user-space daemon, `usbaudiod`. The old `AppleUSBAudioEngine`
driver entry no longer appears in the I/O Registry. MixControl 1.10.6 (the last
release, Intel-only) finds the Scarlett by looking for that entry, so it decides
nothing is connected. Audio keeps working, but the mixer software sees no device.

Focusrite Control and Focusrite Control 2 don't help: they never supported 1st-gen units.

The 1st-gen mixer is driven by plain USB Audio class control requests, and macOS
still lets a normal app send those while `usbaudiod` handles the audio. That's
all this script does.

## Supported hardware

| Device                  | USB ID      | Status            |
|-------------------------|-------------|-------------------|
| Scarlett 18i8 (1st gen) | `1235:8014` | tested, working   |

Other 1st-gen units (6i6, 8i6, 18i6, 18i20) use the same protocol with
different channel counts. Patches are welcome. See
[`mixer_scarlett.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/sound/usb/mixer_scarlett.c)
for their layouts.

Check what you have with:

```sh
system_profiler SPUSBHostDataType | grep -A8 Scarlett    # look for Product ID 0x8014
```

## Install

```sh
brew install libusb
git clone https://github.com/courtenay/scarlett18i8.git
cd scarlett18i8
chmod +x scarlett18i8.py
```

It uses the system `python3` (3.9+) and needs no Python packages. libusb is
loaded through `ctypes`.

## Quick start

> Turn your monitor and headphone knobs down first. Your current settings can't
> be read back (see below), so the first command may change your levels.

```sh
./scarlett18i8.py preset monitor   # analog 1-8 + DAW 1/2 -> Monitor, HP1, HP2
./scarlett18i8.py status           # show what's set
# ...listen, tweak...
./scarlett18i8.py save             # store in the unit so it survives power-off
```

If something goes wrong before you run `save`, power-cycle the 18i8. It goes
back to whatever was last saved to the hardware.

## Commands

```
preset monitor                 starting point: analog 1-8 + DAW 1/2 -> monitors & both phones
status                         show current settings (as stored by this tool) + live sync
apply                          re-send all stored settings (e.g. after a power cycle)
out <1-8> <src>                route a hardware output (1/2 Monitor, 3/4 HP1, 5/6 HP2, 7/8 SPDIF)
vol <master|1-8> <dB>          output volume, -128..+6 dB ('off' = -inf)
mute <master|1-8> <on|off>
matrix <1-18> <src>            choose what feeds mixer input row 1-18 (no mixes; a source
                               already on another row is moved, not doubled)
mix <1-18> <A-H> <dB|off>      set gain of matrix row into Mix A-H
capture <1-14> <src>           choose what the computer receives on input channel 1-14
inst <1|2> <on|off>            instrument (Hi-Z) switch for inputs 1-2
pad <1-4> <on|off>             -10 dB pad for inputs 1-4
clock <internal|spdif|adat>
meters                         live input meters
backup <file.json>             copy the stored settings to a file
restore <file.json>            load a settings file into the device
save                           store current settings in the unit (survives power-off)

<src>: off, pcm1-8 (DAW playback), analog1-8, spdif1-2, adat1-8, mixA-H
```

### Examples

```sh
# Pan input 3 hard right and input 4 hard left (stereo keyboard on 3/4)
./scarlett18i8.py mix 3 A off ; ./scarlett18i8.py mix 3 B 0
./scarlett18i8.py mix 4 A 0   ; ./scarlett18i8.py mix 4 B off

# Separate headphone mix on HP2 using Mix C/D: more DAW, less room mic
./scarlett18i8.py out 5 mixC ; ./scarlett18i8.py out 6 mixD
./scarlett18i8.py mix 17 C 0 ; ./scarlett18i8.py mix 18 D 0
./scarlett18i8.py mix 1 C -12 ; ./scarlett18i8.py mix 1 D -12

# Guitar into input 1
./scarlett18i8.py inst 1 on
```

## How the mixer works

```
 hardware inputs (18) ─┬──────────────────────────────┐
 DAW playback (8)  ────┤                              │
                       ▼                              │
               matrix: pick 18 sources                │
                       ▼                              │
               18 × 8 gain matrix ──► Mix A–H         │
                                         │            │
                 ┌───────────────────────┴────────────┤
                 ▼                                    ▼
        output routing (8)                  capture routing (14)
        1/2 Monitor 3/4 HP1                 → what the computer records
        5/6 HP2     7/8 SPDIF
```

The 18i8 sends 14 channels to the computer (`preset monitor` gives it Analog 1-8,
SPDIF 1-2 and ADAT 1-4). Don't assign the same source to two matrix rows: the
firmware feeds it twice (+6 dB). The `matrix` command moves the source instead.

Mixes are mono busses. For a stereo mix, use a pair (A/B, C/D, …), and pan a
source by setting different gains into the two busses of the pair.

## Important: the tool keeps its own copy of the settings

The 1st-gen hardware **can't report most of its settings**. Reads of routing,
gains and mutes return placeholder values. MixControl handled this by keeping
its own snapshot, and so does this tool: every command updates `state.json`
next to the script, and `status` shows that file, not what's in the hardware.
Only the sample rate, clock lock and meters are read live.

So:

- Make every change through this tool, so `state.json` stays in step with the unit.
- Changes made elsewhere (another computer, an old MixControl install) won't show up here.
- If they get out of step, run `apply` to push the stored settings back to the unit.

## Credits

The USB protocol was reverse-engineered for the Linux kernel's
`sound/usb/mixer_scarlett.c` by Tobias Hoffmann, Robin Gareus, Chris J. Arges
and others. This script reimplements it for macOS in user space. The 14-channel
capture count and the double-feed quirk come from
[MarecekW/scarlett-mixcontrol-1stgen](https://github.com/MarecekW/scarlett-mixcontrol-1stgen).

Not affiliated with or endorsed by Focusrite. "Scarlett" and "MixControl" are
trademarks of Focusrite Audio Engineering Ltd. Use at your own risk.

## License

GPL-2.0-or-later, matching the kernel code the protocol details come from.
See [LICENSE](LICENSE).
