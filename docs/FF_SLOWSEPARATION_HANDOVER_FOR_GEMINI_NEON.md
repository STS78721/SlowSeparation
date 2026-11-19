# FF-SlowSeparation handover for Gemini Neon (2026-11-19)

Fast Fusion (Switch 2, 1.0.0, title `040041E0212AE000`) recompiled to Windows on the Universal NS2 to PC runtime,
packaged as **SteelTemple FF-SlowSeparation**. Read `AGENTS.md` and `docs/UNIVERSAL_NS2_TO_PC_PLAYBOOK.md` first.

## 1. Where everything is

| What | Where |
|---|---|
| Worktree (branch `ff-res-fix`) | `C:\Games\DKB PC\Universal-NS2-to-PC-ff` |
| GitHub repo / branch | `https://github.com/STS78721/SlowSeparation`, `main` = `965ec81` (remote `slowsep`; `ff-res-fix` tracks it) |
| Published release | `Alpha-0.1.0a` (Latest) on commit `965ec81`: Full-Setup-Wizard.exe, Portable-StandAlone.zip, Update.zip |
| Release build output | `<worktree>\build\release-ff\` (`python tools/build_ff_release.py`, ~3 min) |
| Dev game data and build | `C:\Games\Fast Fusion PC\` (`exefs\`, `romfs_packed\`, `generated\ff100\`, `build\ff100\ff.exe` + `ff.ini`) |
| The user's install | `C:\Games\FF-SlowSeparation\` (installed by the wizard; Desktop + Start-menu shortcut "FF SlowSeparation") |
| The user's settings | `C:\Games\FF-SlowSeparation\ff.ini`: `fullscreen = 1`, `window_size = 3200x1800`, `upscaler = fsr`, `[graphics] resolution = 1080` |
| Monitor | 3840x2160 at 225% (DPI 216), RTX 5070 Ti |
| GitHub CLI | `C:\Program Files\GitHub CLI\gh.exe`, signed in as STS78721 |

`main` of the repo's origin (`C:\Games\DKB PC\Universal-NS2-to-PC`, MTF-ShadowRacket work) is **not** merged into
`ff-res-fix`; see section 4. Stay on `ff-res-fix` (the verified runtime) unless that is solved.

## 2. What was done and verified

- **1:1 resolution hooks** (`runtime/resolution.c`, `ff_get_target_resolutions`, `__wrap_f_*`): canvas setters,
  display tier, GetGpuPercent, buffer builders (`ff_maybe_lift_buffer`), C3dFxDlss, and ComCamera (frameBufferScale
  forced to 1.0 before the game's call). `NS2_RESOLUTION` (original|720|900|1080|1440|2160) is the canvas;
  `NS2_RENDER_SCALE` (game = canvas) the scene. Built with 8 `--wrap` symbols in `tools/build_game.py`.
- **Black sky at 1:1 fixed** (`runtime/vk_texture.c` `make_key`): an unset depth-stencil mode keys as DEPTH, so the sky
  shader (`96b7b3b96d984bcf`) reads the image the RenderQueue_DepthCopy copy writes.
- **Packaging (MTF-ShadowRacket style)**: `launcher/FFLauncher.py` + `launcher/ff_dump.py`, `tools/installer_ff/`,
  `tools/build_ff_release.py`, `FFLauncher.spec`, `ff.ini.example`, `tools/pack_romfs.py`, `tools/make_ff_icons.py`
  (original icons), `runtime/ff.rc`. F1 menu from main (`runtime/ui/*`, `theme.h`) with the SlowSeparation theme,
  `NS2_MENU_THEME`, `NS2_ANISO`.
- **Setup 5/5 shader pre-warm** (`FFLauncher._prewarm_shaders`): `ff.exe --seconds 50 --profile work/warm-profile`
  with `tools/data/ff_warm_route.txt`, then `ff.exe --prepare-shaders` (242 variants, 0 failed). Note: async pipelines
  and shader recording are off when `--shot` is passed (`runtime/boot.c`), so the warm run must not use `--shot`. The
  census seed (`tools/prepare_shaders.py`) does not work for FF: all its default variants fail in the translator.
- Verified: Track 1 race with blue sky at 1080p, 1440p (2560x1440) and 4K, 1:1; installed build end to end (wizard,
  launcher setup from the dump, race); `--selftest` 0 failures; 0 validation errors.

## 3. OPEN: "fullscreen at 1440p or 4K internal makes the game way larger than it should"

Reported by the user on their 4K monitor, fullscreen. **Not reproduced yet.** What was tried (all fullscreen on the
user's monitor, screenshots with desktop capture, route `tools/data/ff_warm_route.txt`):

1. Installed `ff.exe` with the user's `ff.ini` + `NS2_RESOLUTION=1440`: picture fits the screen (HUD in its corners,
   racer ~290 px wide in a 1456 px capture, same as a known-good run). Log: canvas and scene 2560x1440, `window
   3840x2160 ... swapchain 3200x1800`, FSR upscales 2560x1440 to 3200x1800.
2. Dev build, `NS2_UPSCALER=fsr`, `NS2_WINDOW_SIZE=3200x1800`, 1440: fits.
3. Dev build, upscaler off: fits.

Do not compare the gate's size between screenshots taken at different race times: the car creeps forward; compare
the racer's width and HUD instead.

Leads, most likely first:
- **The swapchain stays at `window_size` (3200x1800) on a 3840x2160 fullscreen window** (`runtime/gfx_present.c`
  `create_swapchain` takes `caps.currentExtent`, which reported 3200x1800). Windows scales it evenly (16:9 to 16:9) so
  it looked right here, but with a different `window_size` aspect (or DPI scaling settings) it would stretch. A size
  check in `present_image` was tried and reverted: `currentExtent` never differed from the swapchain, so it never
  fired. Ask what `window_size` was set when it happened.
- **Live changes from the F1 menu**: `RESOLUTION` is a restart setting; the user may have changed Display options
  (window size, output) live in fullscreen. Reproduce by changing them in F1 during a race.
- **`nvnWindowSetCrop` un-clamp** (`runtime/nvn_api_obj.c`): widens the crop to the texture only when the game asks
  for <= 1920x1080; the game asks for a 3840x2160 docked canvas. Log `WindowCrop` with `NVN_LOG` to see the crop.
- First step: **get a screenshot from the user** of the bad image and the exact settings (launcher Graphics page and
  F1 Display/Graphics pages), then reproduce with those.

## 4. OPEN: main's runtime blacks out Fast Fusion

A trial `git merge main` into `ff-res-fix` (conflicts only in `runtime/cfg.c`, `runtime/resolution.c`,
`tools/build_game.py`) builds, but the race renders almost entirely black (only emissive strips and the HUD), even
with `NS2_SHADER_CACHE=0 NS2_PIPELINE_CACHE=0`. A Claude session started to bisect it was stopped before it reported.
Suspects in `git diff 42594ba main --stat`: `runtime/vk_exec_draw.c`, `vk_pipeline.c`, `gfx_shader.c`,
`third_party/eglantine/.../lower_to_maxwell.h`, `vk_texture.c` (MTF D24 shadow compare, border colour heuristic),
`vk_exec_xfer.c`, cfg defaults. Use the frame dump (`NS2_DUMP=1400 NS2_DUMP_SKIP_RUNS=1 NS2_DUMP_DIR=<dir>`,
`runtime/vk_dump.c`): in a good frame the deferred lighting composite is pass ~73 and the sky pass 76.

## 5. Recipes

Build (dev):

```
python tools/build_game.py --version ff100 --gen "C:\Games\Fast Fusion PC\generated\ff100" --out "C:\Games\Fast Fusion PC\build\ff100" --exefs "C:\Games\Fast Fusion PC\exefs" --romfs "C:\Games\Fast Fusion PC\romfs_packed" --profile "C:\Games\DKB PC\Universal-NS2-to-PC\build\profile-ff100"
```

Test run into the first race (logs go to stdout with `--seconds`):

```
NS2_FULLSCREEN=0 NS2_INPUT_SCRIPT=<worktree>\tools\data\ff_warm_route.txt ff.exe --seconds 40 --shot out.png
```

`--shot` saves the game's own buffer (`runtime/boot.c` `save_shot`), not the presented swapchain: scaling bugs in the
present path need a real desktop screenshot. Use `--profile <scratch dir>` to keep the user's saves untouched.

Release: `python tools/build_ff_release.py` (needs `C:\Games\MKW PC\upstream-mkw-recomp\build` caches, PyInstaller in
the main checkout's `.venv`, FF's `ns2_xlate.exe` from `C:\Games\Fast Fusion PC\build\ff100`). Publish with
`gh release create <tag> -R STS78721/SlowSeparation --target <full sha> --title ... --notes-file ... <3 files>`;
FFLauncher's updater looks for `SteelTemple-FF-SlowSeparation-<tag>-Update.zip`.

Rules: commit messages are plain sentences; run `python tools/check_content.py --staged` before committing; no game
data in the repo; never edit generated C.
