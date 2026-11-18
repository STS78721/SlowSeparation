// Game resolution: the size the game composes its frame in (its output: canvas, UI and swapchain) and the size of its
// 3D scene, above the shipped 1920x1080 and 1600x900. The game does the rendering itself, as with the community
// resolution mods (UltraCam, Visual Fixes): the patch sets output and scene (recomp/patches-v121.txt,
// patches-v143.txt; MacNativeGames games/totk/runtime/game_patches.c and game_patches_v143.c, MIT, Copyright (c) 2026
// MacNativeGames contributors) make the game read both sizes from the patch page, and the texture heap that holds the
// output- and scene-sized render targets grows with them.
//
// Settings, read at start: TOTK_OUTPUT_RES = <w>x<h> (default 1920x1080, the game's own), TOTK_RENDER_SCALE = the
// scene's size relative to the output: "game" (default, 5/6, the game's 1600x900 at 1080p) or a percentage; above 100
// the game supersamples its scene, which smooths edges (anti-aliasing) at the cost of GPU time. The game's own final
// pass (FSR 1 EASU) scales the scene to the output; the present scales the output to the window (vk_fsr.c).
//
// Patch page: +16 the scene size {w, h} as floats, +32 the output size and +48 the swapchain size as {w, h} in one
// 64-bit word, +40 the renderer (1.2.1) or display configuration (1.4.3) the patched code stores there.
// +56 the camera's aspect ratio as a float when the output is not 16:9 (set aspect), +60 the field-of-view factor
// (set fov, res_fov_live).
// 1.4.3 scales the scene it reads by output width / 1920 (a 2560x1440 output renders its 1600x900 scene at 2128x1200;
// MacNativeGames measured the same), so its page holds the wanted size divided by that factor and the game's 5/6 needs
// no scene patch. 1.2.1 renders the size it reads (1600x900 on a 2560x1440 output): its page holds the wanted size, and
// the scene set is on whenever the output differs from 1920x1080.
//
// Heap: agl's DynamicTextureAllocator is one fixed heap, "DynamicTextureAllocatorMem" in
// System/HeapSize/Product.Nin_NX_NVN.HeapDef.byml.zs (113664 KiB, carved from "Graphics", 201728 KiB). At 2560x1440
// the shipped size runs out at the title screen. Both heaps grow by the same amount: the shipped size times the output's
// pixel ratio above 1920x1080 plus the scene's above 1600x900 (MacNativeGames' measurement: a 3440x1440 canvas with a
// 4620x1935 scene crashed at +258 MiB and loaded at +400 MiB; this formula gives +750 MiB there). romfs.c serves the
// rewritten table (res_heapdef_build) in place of the original; memory.c adds the amount to the application pool, so
// no other heap shrinks. Without the rewritten table (a RomFS that lacks it, a format this code does not know) the
// patch sets stay off and the game keeps its own sizes.
#include <ctype.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zstd.h>

#include "cfg.h"
#include "game_profile.h"
#include "host.h"
#include "platform/platform.h"

#define BASE_W 1920
#define BASE_H 1080
#define SCENE_W 1600
#define SCENE_H 900
#define DTA_KIB 113664u      // DynamicTextureAllocatorMem, shipped
#define SCENE_MAX 16384      // per side, up to 16K on modern PC GPUs

static struct {
    int decided;
    unsigned ow, oh, sw, sh;   // output and scene
    int scene_set;             // the scene differs from the game's 5/6 of the output
    int heapdef_ok;            // romfs.c serves the grown HeapDef
    int on;                    // res_init switched the patch sets on
} R;

static volatile uint32_t g_live_win_w, g_live_win_h, g_live_crop_w, g_live_crop_h;

void res_record_game_surface(uint32_t win_w, uint32_t win_h, int cx, int cy, int cw, int ch) {
    (void)cx;
    (void)cy;
    if (win_w > 0 && win_h > 0) {
        __atomic_store_n(&g_live_win_w, win_w, __ATOMIC_RELAXED);
        __atomic_store_n(&g_live_win_h, win_h, __ATOMIC_RELAXED);
    }
    if (cw > 0 && ch > 0) {
        __atomic_store_n(&g_live_crop_w, (uint32_t)cw, __ATOMIC_RELAXED);
        __atomic_store_n(&g_live_crop_h, (uint32_t)ch, __ATOMIC_RELAXED);
    } else if (win_w > 0 && win_h > 0) {
        __atomic_store_n(&g_live_crop_w, win_w, __ATOMIC_RELAXED);
        __atomic_store_n(&g_live_crop_h, win_h, __ATOMIC_RELAXED);
    }
}

static void decide(void) {
    static char last_out[64] = {0};
    static char last_scale[64] = {0};

    const char *o = cfg_value("TOTK_OUTPUT_RES");
    const char *s = cfg_value("TOTK_RENDER_SCALE");
    if (!o) o = "1920x1080";
    if (!s) s = "game";

    if (R.decided && !strcmp(o, last_out) && !strcmp(s, last_scale))
        return;
    R.decided = 1;
    snprintf(last_out, sizeof last_out, "%s", o);
    snprintf(last_scale, sizeof last_scale, "%s", s);

    R.ow = BASE_W;
    R.oh = BASE_H;
    unsigned w = 0, h = 0;
    char tail;
    if (sscanf(o, "%ux%u%c", &w, &h, &tail) == 2 && w >= 1280 && h >= 720 && w <= 7680 && h <= 4320) {
        R.ow = w & ~1u;
        R.oh = h & ~1u;
    } else {
        rt_log("resolution: TOTK_OUTPUT_RES=%s is not <width>x<height> in 1280x720..7680x4320; 1920x1080", o);
    }

    double k = 1.0;
    if (strcmp(s, "game")) {
        char *end = NULL;
        double val = strtod(s, &end);
        if (end && (*end == 'x' || *end == 'X')) {
            k = (R.ow >= 3840) ? (val * 0.5) : val; // 1x = 1080p, 2x = 4K, 3x = 6K, 4x = 8K
            R.scene_set = 1;
        } else if (val >= 1.0 && val <= 8.0 && val == (int)val && end != s && !*end) {
            k = (R.ow >= 3840) ? (val * 0.5) : val;
            R.scene_set = 1;
        } else if (end != s && !*end && val >= 25 && val <= 800) {
            k = val / 100.0;
            R.scene_set = 1;
        } else {
            rt_log("resolution: TOTK_RENDER_SCALE=%s is not game or a percentage/multiplier; using game", s);
        }
    }
    double sw = R.ow * k, sh = R.oh * k;
    double fit = fmin(1.0, fmin(SCENE_MAX / sw, SCENE_MAX / sh));
    if (fit < 1.0) R.scene_set = 1;
    R.sw = (unsigned)(sw * fit + 0.5) & ~1u;
    R.sh = (unsigned)(sh * fit + 0.5) & ~1u;
}

static int bigger(void) {
    decide();
    return R.ow != BASE_W || R.oh != BASE_H || R.scene_set;
}

uint64_t res_heap_extra_kib(void) {
    if (!bigger()) return 0;
    double grow = fmax(0.0, (double)R.ow * R.oh / (BASE_W * BASE_H) - 1.0) +
                  fmax(0.0, (double)R.sw * R.sh / (SCENE_W * SCENE_H) - 1.0);
    if (grow <= 0) return 0;
    return (uint64_t)ceil(DTA_KIB * (1.0 + grow) / 1024.0) * 1024 - DTA_KIB;
}

uint64_t res_heap_extra_bytes(void) { return ((res_heap_extra_kib() << 10) + 0x1fffff) & ~0x1fffffull; }

// ---- the HeapDef table. BYML version 2..7, little endian ("YB"): a hash node is {u8 0xC1, u24 count} and count
// entries {u24 key index, u8 type, u32 value}; an int (type 0xD1) is stored in the entry, so a size changes in place.
typedef struct { uint8_t *p; size_t n; uint32_t keys; } Byml;

static int u32_at(const Byml *b, size_t off, uint32_t *v) {
    if (off + 4 > b->n) return 0;
    memcpy(v, b->p + off, 4);
    return 1;
}

static const char *key_name(const Byml *b, uint32_t k) {
    uint32_t head, at;
    if (!u32_at(b, b->keys, &head) || (head & 0xff) != 0xc2 || k >= head >> 8 || !u32_at(b, b->keys + 4 + 4 * k, &at) ||
        b->keys + at >= b->n || !memchr(b->p + b->keys + at, 0, b->n - b->keys - at))
        return NULL;
    return (const char *)b->p + b->keys + at;
}

// The offset of the value of hash node `node`'s entry `name`, which must have type `type`; 0 when there is none.
static size_t field(const Byml *b, uint32_t node, const char *name, uint8_t type) {
    uint32_t head;
    if (!u32_at(b, node, &head) || (head & 0xff) != 0xc1) return 0;
    for (uint32_t i = 0; i < head >> 8; i++) {
        uint32_t e;
        if (!u32_at(b, node + 4 + 8 * (size_t)i, &e)) return 0;
        const char *k = key_name(b, e & 0xffffff);
        if (k && !strcmp(k, name)) return e >> 24 == type ? node + 8 + 8 * (size_t)i : 0;
    }
    return 0;
}

static size_t heap_size_field(const Byml *b, uint32_t heaps, const char *heap) {
    size_t at = field(b, heaps, heap, 0xc1);
    uint32_t node;
    return at && u32_at(b, at, &node) ? field(b, node, "Size", 0xd1) : 0;
}

int res_heapdef_build(const void *in, size_t n, void **out, size_t *out_n) {
    uint64_t extra = res_heap_extra_kib();
    *out = NULL;
    if (!extra) return 0;
    if (ZSTD_getDictID_fromFrame(in, n)) {
        rt_log("resolution: the HeapDef table needs a zstd dictionary; the game keeps its own resolution");
        return 0;
    }
    unsigned long long raw = ZSTD_getFrameContentSize(in, n);
    if (raw == ZSTD_CONTENTSIZE_UNKNOWN || raw == ZSTD_CONTENTSIZE_ERROR || raw > (64u << 20)) {
        rt_log("resolution: the HeapDef table is not a zstd frame with a size; the game keeps its own resolution");
        return 0;
    }
    Byml b = {malloc((size_t)raw), (size_t)raw, 0};
    if (!b.p) return 0;
    size_t got = ZSTD_decompress(b.p, b.n, in, n);
    uint32_t root, heaps = 0;
    uint16_t ver = 0;
    if (!ZSTD_isError(got) && got == b.n && b.n >= 16) memcpy(&ver, b.p + 2, 2);
    size_t heaps_at = 0, dta_at = 0, gfx_at = 0;
    if (ver >= 2 && ver <= 7 && !memcmp(b.p, "YB", 2) && u32_at(&b, 4, &b.keys) && u32_at(&b, 12, &root) &&
        (heaps_at = field(&b, root, "Heaps", 0xc1)) && u32_at(&b, heaps_at, &heaps)) {
        dta_at = heap_size_field(&b, heaps, "DynamicTextureAllocatorMem");
        gfx_at = heap_size_field(&b, heaps, "Graphics");
    }
    if (!dta_at || !gfx_at) {
        rt_log("resolution: the HeapDef table has no DynamicTextureAllocatorMem / Graphics sizes; the game keeps its own "
               "resolution");
        free(b.p);
        return 0;
    }
    int32_t dta, gfx;
    memcpy(&dta, b.p + dta_at, 4);
    memcpy(&gfx, b.p + gfx_at, 4);
    int32_t dta2 = dta + (int32_t)extra, gfx2 = gfx + (int32_t)extra;
    memcpy(b.p + dta_at, &dta2, 4);
    memcpy(b.p + gfx_at, &gfx2, 4);
    size_t cap = ZSTD_compressBound(b.n);
    void *z = malloc(cap);
    size_t zn = z ? ZSTD_compress(z, cap, b.p, b.n, 9) : 0;
    free(b.p);
    if (!z || ZSTD_isError(zn)) {
        free(z);
        return 0;
    }
    *out = z;
    *out_n = zn;
    rt_log("resolution: HeapDef DynamicTextureAllocatorMem %d -> %d KiB, Graphics %d -> %d KiB", dta, dta2, gfx, gfx2);
    return 1;
}

void res_heapdef_served(void) { R.heapdef_ok = 1; }

// ---- start: the patch page and the sets
static uint64_t pack(unsigned w, unsigned h) { return (uint64_t)h << 32 | w; }

void res_init(void) {
    if (!game_profile_has(GAME_QUIRK_TOTK_PATCH_RES)) {
        const RtGameProfile *gp = game_profile();
        if (res_ff_enabled()) {
            uint32_t cw = 0, ch = 0, sw = 0, sh = 0;
            ff_get_target_resolutions(&cw, &ch, &sw, &sh);
            rt_log("resolution: Fast Fusion resolution hooks active (Canvas %ux%u, Scene %ux%u)", cw, ch, sw, sh);
            return;
        }
        rt_log("resolution: Universal NS2 live hooks active for %s (Docked %ux%u, Handheld %ux%u)",
               gp->display_name, gp->docked_w, gp->docked_h, gp->handheld_w, gp->handheld_h);
        return;
    }
    if (!bigger()) {
        rt_log("resolution: the game's own (output 1920x1080, scene 1600x900)");
        return;
    }
    int output = rt_patch_set("output"), scene = rt_patch_set("scene");
    if (output < 0 || scene < 0) {
        rt_log("resolution: the generated C has no output and scene patch sets; the game keeps its own resolution");
        return;
    }
    fs_init();   // romfs.c serves the grown HeapDef from here on (res_heapdef_served)
    if (res_heap_extra_kib() && !R.heapdef_ok) {   // sizes at or below the shipped ones fit the shipped heaps
        rt_log("resolution: no grown HeapDef table; the game keeps its own resolution");
        return;
    }
    uint8_t *page = rt_patch_page();
    double k = RT_V143 ? (double)R.ow / BASE_W : 1.0;   // 1.4.3's own scaling of the scene with the output width
    int scene_on = R.scene_set || (!RT_V143 && (R.ow != BASE_W || R.oh != BASE_H));
    float sc[2] = {(float)(R.sw / k), (float)(R.sh / k)};
    uint64_t out = pack(R.ow, R.oh), none = 0;
    memcpy(page + 16, sc, sizeof sc);
    memcpy(page + 32, &out, 8);
    memcpy(page + 40, &none, 8);
    memcpy(page + 48, &out, 8);
    rt_patch_switch(output, R.ow != BASE_W || R.oh != BASE_H);
    rt_patch_switch(scene, scene_on);
    R.on = 1;
    // Another shape than 16:9 (more than 1% off): the camera takes the output's ratio (recomp/patches-v121.txt and
    // patches-v143.txt, set aspect), so a 21:9 output shows more of the world at the sides instead of a stretched 16:9 view. The HUD's layouts
    // for that shape come from romfs-patches-<w>x<h> (romfs.c overlay_init, tools/make_romfs_patches.py --aspect).
    double ratio = (double)R.ow / R.oh, game = (double)BASE_W / BASE_H;
    int aspect = rt_patch_set("aspect");
    if (!res_is_16x9(R.ow, R.oh)) {
        if (aspect < 0) {
            rt_log("resolution: output %ux%u is not 16:9 and this game version has no camera aspect patch; the view "
                   "is stretched", R.ow, R.oh);
        } else {
            float r = (float)ratio;
            memcpy(page + 56, &r, sizeof r);
            rt_patch_switch(aspect, 1);
            rt_log("resolution: camera aspect ratio %.4f (16:9 is %.4f)", ratio, game);
        }
    } else {
        rt_patch_switch(aspect, 0);   // the page holds no ratio
    }
    rt_log("resolution: output %ux%u, scene %ux%u%s; texture heap +%llu KiB", R.ow, R.oh, R.sw, R.sh,
           scene_on ? "" : " (the game's own 5/6 of the output)", (unsigned long long)res_heap_extra_kib());
}

// 16:9 within 1% (1366x768 counts): the shape the game's own layouts and camera are made for.
int res_is_16x9(unsigned w, unsigned h) {
    double rel = ((double)w / h) / ((double)BASE_W / BASE_H);
    return rel >= 0.99 && rel <= 1.01;
}

// The game resolution asks for a romfs-patches-<w>x<h> overlay: it is not 16:9 and the game version has the camera
// aspect patch (set aspect); the UI layouts of another shape only fit a camera that follows the same shape.
int res_wants_wide_overlay(const unsigned out[2]) {
    return !res_is_16x9(out[0], out[1]) && rt_patch_set("aspect") >= 0;
}

// Field of view (TOTK_FOV, percent of the game's own, 70..140, live): the camera's vertical view angle times the
// factor on page +60 (recomp/patches-v143.txt, set fov). The horizontal angle follows through the aspect ratio. 100
// switches the set off, so the camera code is the game's own. Runs on a settings change (gfx_present.c
// window_settings_live), the first time at the first present.
#define FOV_MIN 70
#define FOV_MAX 140
void res_fov_live(void) {
    static int set = -2, shown = 100;
    if (set == -2) set = rt_patch_set("fov");
    int pct = cfg_int("TOTK_FOV");
    pct = pct < FOV_MIN ? FOV_MIN : pct > FOV_MAX ? FOV_MAX : pct;
    if (pct == shown) return;
    shown = pct;
    if (set < 0) {
        if (game_profile_has(GAME_QUIRK_TOTK_PATCH_RES))
            rt_log("resolution: this game version has no field-of-view patch; TOTK_FOV=%d does nothing", pct);
        return;
    }
    float k = pct / 100.0f;
    memcpy((uint8_t *)rt_patch_page() + 60, &k, sizeof k);   // before the switch: a site that sees it on reads k
    rt_patch_switch(set, pct != 100);
    rt_log("resolution: field of view %d%% of the game's", pct);
}

void res_wanted(unsigned out[2]) {
    if (res_ff_enabled()) {
        uint32_t cw = 0, ch = 0;
        ff_get_target_resolutions(&cw, &ch, NULL, NULL);
        out[0] = cw;
        out[1] = ch;
        return;
    }
    decide();
    out[0] = R.ow, out[1] = R.oh;
}

void res_sizes(unsigned out[2], unsigned scene[2]) {
    if (res_ff_enabled()) {
        uint32_t cw = 0, ch = 0, sw = 0, sh = 0;
        ff_get_target_resolutions(&cw, &ch, &sw, &sh);
        out[0] = cw;
        out[1] = ch;
        scene[0] = sw;
        scene[1] = sh;
        return;
    }
    decide();
    out[0] = R.ow ? R.ow : BASE_W;
    out[1] = R.oh ? R.oh : BASE_H;
    scene[0] = R.sw ? R.sw : SCENE_W;
    scene[1] = R.sh ? R.sh : SCENE_H;
}

void res_apply_live(void) {
    static int init = 0;
    static int prev_handheld = -1;
    static char prev_out[64] = {0};

    const char *op = cfg_value("NS2_OPERATION_MODE");
    int hh = (op && !strcmp(op, "handheld")) ? 1 : 0;
    const char *out_res = cfg_value("NS2_OUTPUT_RES");

    if (!init) {
        init = 1;
        prev_handheld = hh;
        if (out_res) snprintf(prev_out, sizeof prev_out, "%s", out_res);
        decide();
        // Synchronize window size on initial startup if set
        if (out_res && !cfg_bool("NS2_FULLSCREEN") && strcmp(cfg_value("NS2_WINDOW_SIZE"), out_res) != 0) {
            cfg_set("NS2_WINDOW_SIZE", out_res);
        }
        return;
    }

    int notify_mode = (hh != prev_handheld);
    if (notify_mode) prev_handheld = hh;

    int out_changed = out_res && strcmp(out_res, prev_out) != 0;
    if (out_changed) {
        snprintf(prev_out, sizeof prev_out, "%s", out_res);
        R.decided = 0;
        decide();
        // Keep host window swapchain synchronized with Output Resolution when windowed
        if (!cfg_bool("NS2_FULLSCREEN") && strcmp(cfg_value("NS2_WINDOW_SIZE"), out_res) != 0) {
            cfg_set("NS2_WINDOW_SIZE", out_res);
        }
        notify_mode = 1;
    }
    if (notify_mode) ov_notify_operation_mode_changed();
}

void res_live_snapshot(unsigned win_sz[2], unsigned crop_sz[2], unsigned out_tgt[2], int *handheld) {
    uint32_t ww = __atomic_load_n(&g_live_win_w, __ATOMIC_RELAXED);
    uint32_t wh = __atomic_load_n(&g_live_win_h, __ATOMIC_RELAXED);
    uint32_t cw = __atomic_load_n(&g_live_crop_w, __ATOMIC_RELAXED);
    uint32_t ch = __atomic_load_n(&g_live_crop_h, __ATOMIC_RELAXED);
    if (win_sz) {
        win_sz[0] = ww ? ww : game_profile()->docked_w;
        win_sz[1] = wh ? wh : game_profile()->docked_h;
    }
    if (crop_sz) {
        crop_sz[0] = cw ? cw : (ww ? ww : game_profile()->docked_w);
        crop_sz[1] = ch ? ch : (wh ? wh : game_profile()->docked_h);
    }
    if (out_tgt) {
        decide();
        out_tgt[0] = R.ow;
        out_tgt[1] = R.oh;
    }
    if (handheld) {
        const char *op = cfg_value("NS2_OPERATION_MODE");
        *handheld = (op && !strcmp(op, "handheld")) ? 1 : 0;
    }
}

// ---- Fast Fusion Resolution Scaling Hooks
// Configuration priority: TOTK_RESOLUTION -> TOTK_OUTPUT_RES -> game_profile() (1920x1080 docked, 1280x720 handheld);
// TOTK_RENDER_SCALE="game" defaults 1:1. TOTK_RES_HOOK=0 bypasses all hooks.
extern void __real_f_700011ff04(Cpu *c) __attribute__((weak));
extern void __real_f_700011ff1c(Cpu *c) __attribute__((weak));
extern void __real_f_700011fde4(Cpu *c) __attribute__((weak));
extern void __real_f_700006ec74(Cpu *c) __attribute__((weak));
extern void __real_f_70000f0960(Cpu *c) __attribute__((weak));
extern void __real_f_70001080b0(Cpu *c) __attribute__((weak));
extern void __real_f_7000108170(Cpu *c) __attribute__((weak));
extern void __real_f_700002c764(Cpu *c) __attribute__((weak));

int res_ff_enabled(void) {
    const RtGameProfile *gp = game_profile();
    return gp && gp->short_name && !strcmp(gp->short_name, "ff") && cfg_bool("TOTK_RES_HOOK");
}

static int ff_streqi(const char *a, const char *b) {
    for (; *a && *b; a++, b++) {
        if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) return 0;
    }
    return *a == *b;
}

static int ff_strcasestr(const char *hay, const char *needle) {
    size_t nlen = strlen(needle);
    if (!nlen) return 1;
    for (; *hay; hay++) {
        size_t i = 0;
        while (i < nlen && hay[i] && tolower((unsigned char)hay[i]) == tolower((unsigned char)needle[i])) i++;
        if (i == nlen) return 1;
    }
    return 0;
}

static int ff_parse_resolution(const char *s, uint32_t *out_w, uint32_t *out_h) {
    if (!s || !*s) return 0;
    if (ff_streqi(s, "original") || ff_streqi(s, "default") || ff_streqi(s, "game")) return 0;
    if (ff_streqi(s, "4k")) {
        *out_w = 3840u;
        *out_h = 2160u;
        return 1;
    }
    unsigned w = 0, h = 0;
    char tail = 0;
    if (sscanf(s, "%ux%u%c", &w, &h, &tail) == 2 && w >= 640u && h >= 360u && w <= 16384u && h <= 16384u) {
        *out_w = w & ~1u;
        *out_h = h & ~1u;
        return 1;
    }
    char *end = NULL;
    unsigned long val = strtoul(s, &end, 10);
    if (end != s && (*end == 0 || ((*end == 'p' || *end == 'P') && end[1] == 0)) && val >= 360ul && val <= 4320ul) {
        uint32_t th = (uint32_t)val & ~1u;
        *out_w = ((uint32_t)((uint64_t)th * 16u / 9u)) & ~1u;
        *out_h = th;
        return 1;
    }
    return 0;
}

void ff_get_target_resolutions(uint32_t *canvas_w, uint32_t *canvas_h, uint32_t *scene_w, uint32_t *scene_h) {
    const RtGameProfile *gp = game_profile();
    const char *op = cfg_value("TOTK_OPERATION_MODE");
    int hh = (op && !strcmp(op, "handheld")) ? 1 : 0;
    uint32_t base_w = (gp && (hh ? gp->handheld_w : gp->docked_w)) ? (hh ? gp->handheld_w : gp->docked_w) : (hh ? 1280u : 1920u);
    uint32_t base_h = (gp && (hh ? gp->handheld_h : gp->docked_h)) ? (hh ? gp->handheld_h : gp->docked_h) : (hh ? 720u : 1080u);

    uint32_t cw = base_w, ch = base_h;
    const char *res_opt = cfg_get("TOTK_RESOLUTION");
    if (!ff_parse_resolution(res_opt, &cw, &ch)) {
        const char *o = cfg_get("TOTK_OUTPUT_RES");
        unsigned w = 0, h = 0;
        char tail = 0;
        if (o && *o && sscanf(o, "%ux%u%c", &w, &h, &tail) == 2 && w >= 640u && h >= 360u && w <= 16384u && h <= 16384u) {
            cw = w & ~1u;
            ch = h & ~1u;
        }
    }

    uint32_t sw = cw, sh = ch;
    const char *s = cfg_value("TOTK_RENDER_SCALE");
    if (s && *s && strcmp(s, "game") && !ff_streqi(s, "native")) {
        if (ff_streqi(s, "4k")) {
            sw = 3840u;
            sh = 2160u;
        } else {
            char *end = NULL;
            double val = strtod(s, &end);
            if (end != s && (*end == 'x' || *end == 'X') && end[1] == 0) {
                double mult = val < 0.25 ? 0.25 : val > 4.0 ? 4.0 : val;
                sw = (uint32_t)(base_w * mult + 0.5) & ~1u;
                sh = (uint32_t)(base_h * mult + 0.5) & ~1u;
            } else if (end != s && *end == 0 && val >= 1.0 && val <= 8.0 && val == (int)val) {
                double mult = val > 4.0 ? 4.0 : val;
                sw = (uint32_t)(base_w * mult + 0.5) & ~1u;
                sh = (uint32_t)(base_h * mult + 0.5) & ~1u;
            } else if (end != s && *end == 0 && val >= 25.0 && val <= 800.0) {
                double pct = val / 100.0;
                sw = (uint32_t)(cw * pct + 0.5) & ~1u;
                sh = (uint32_t)(ch * pct + 0.5) & ~1u;
            }
        }
    }

    if (sw < 640u) sw = 640u;
    if (sh < 360u) sh = 360u;
    if (sw > 7680u) sw = 7680u;
    if (sh > 4320u) sh = 4320u;

    if (canvas_w) *canvas_w = cw;
    if (canvas_h) *canvas_h = ch;
    if (scene_w) *scene_w = sw;
    if (scene_h) *scene_h = sh;
}

static inline void ff_call_real(GuestFn real_fn, uint64_t guest_addr, Cpu *c) {
    if (real_fn) {
        real_fn(c);
        return;
    }
    GuestFn orig = rt_dispatch_lookup(guest_addr);
    if (orig) orig(c);
}

static void ff_wrap_disp_canvas(Cpu *c, GuestFn real_fn, uint64_t guest_addr, const char *slot_name) {
    if (res_ff_enabled()) {
        uint64_t disp_cfg = plat_mem_readable(0x700071beb8ull, 8) ? LD64(0x700071beb8ull) : 0;
        if ((disp_cfg && c->x[0] == disp_cfg) || (c->x[1] >= 640u && c->x[2] >= 360u)) {
            uint32_t cw = 0, ch = 0;
            ff_get_target_resolutions(&cw, &ch, NULL, NULL);
            if ((uint32_t)c->x[1] != cw || (uint32_t)c->x[2] != ch)
                rt_log("ff: %s canvas %ux%u -> %ux%u", slot_name, (unsigned)c->x[1], (unsigned)c->x[2], cw, ch);
            c->x[1] = cw;
            c->x[2] = ch;
        }
    }
    ff_call_real(real_fn, guest_addr, c);
}

void __wrap_f_700011ff04(Cpu *c) { ff_wrap_disp_canvas(c, __real_f_700011ff04, 0x700011ff04ull, "docked"); }
void __wrap_f_700011ff1c(Cpu *c) { ff_wrap_disp_canvas(c, __real_f_700011ff1c, 0x700011ff1cull, "handheld"); }

void __wrap_f_700011fde4(Cpu *c) {
    if (res_ff_enabled() && c->x[0] && plat_mem_readable(c->x[0] + 0x70a0ull, 4)) {
        uint32_t tier = LD32(c->x[0] + 0x70a0ull);
        if (tier < 3u) {
            ST32(c->x[0] + 0x70a0ull, 3u);
            static int logged = 0;
            if (!logged) {
                logged = 1;
                rt_log("ff: lifted display tier %u -> 3 (uncapped)", tier);
            }
        }
    }
    ff_call_real(__real_f_700011fde4, 0x700011fde4ull, c);
}

void __wrap_f_700006ec74(Cpu *c) {
    uint64_t cam = c->x[0];
    if (res_ff_enabled() && cam && plat_mem_readable(cam + 0x94ull, 8)) {
        uint32_t bx = LD32(cam + 0x94ull), by = LD32(cam + 0x98ull);
        float sx = 1.0f, sy = 1.0f;
        memcpy(&sx, &bx, 4);
        memcpy(&sy, &by, 4);
        if (sx != 1.0f || sy != 1.0f) {
            ST32(cam + 0x94ull, 0x3f800000u);
            ST32(cam + 0x98ull, 0x3f800000u);
            static int logged = 0;
            if (!logged) {
                logged = 1;
                rt_log("ff: ComCamera frameBufferScale (%.3f, %.3f) -> 1.000", sx, sy);
            }
        }
    }
    ff_call_real(__real_f_700006ec74, 0x700006ec74ull, c);
}

void __wrap_f_70000f0960(Cpu *c) {
    if (res_ff_enabled()) {
        static int logged = 0;
        if (!logged) {
            logged = 1;
            rt_log("ff: GetGpuPercent -> 3.0%% (DRS disabled)");
        }
        ret_s(c, 3.0f);
        return;
    }
    ff_call_real(__real_f_70000f0960, 0x70000f0960ull, c);
}

static void ff_read_guest_cstr(uint64_t ptr, char *out, size_t cap) {
    out[0] = 0;
    if (!ptr || cap < 2) return;
    size_t i = 0;
    for (; i + 1 < cap; i++) {
        if (!plat_mem_readable(ptr + i, 1)) break;
        char ch = (char)LD8(ptr + i);
        if (!ch) break;
        out[i] = (ch >= 0x20 && ch < 0x7f) ? ch : '?';
    }
    out[i] = 0;
}

static int ff_is_aux_buffer(const char *name) {
    if (ff_streqi(name, "map") || ff_streqi(name, "mapdepth")) return 1;
    if (ff_strcasestr(name, "gtao res") && !ff_strcasestr(name, "depthres")) return 1;
    static const char *const kAux[] = {
        "shadow", "cube", "dummy", "temp", "mip", "bloom", "streak", "godray",
        "flare", "sao", "blur", "fog", "lut", "tile", "exposure", "scaledown", "bilateral",
    };
    for (size_t i = 0; i < sizeof kAux / sizeof *kAux; i++) {
        if (ff_strcasestr(name, kAux[i])) return 1;
    }
    return 0;
}

static int ff_is_canvas_buffer(const char *name) {
    return ff_strcasestr(name, "framebuffer") || ff_strcasestr(name, "cbhud") || ff_strcasestr(name, "cdlss::");
}

static int ff_is_scene_target(const char *name) {
    static const char *const kScene[] = {
        "dyna_cb", "dyna_db", "vehiclecb", "vehicledb", "deferred", "smoothnormals",
        "fxcapture", "gtao depthres", "screenspacereflection", "reflectionstarget",
        "reflection_distance_depth", "motionblur", "renderqueue_depthcopy",
        "screentransition", "displaybuffer", "disp0", "disp1", "disp2",
    };
    for (size_t i = 0; i < sizeof kScene / sizeof *kScene; i++) {
        if (ff_strcasestr(name, kScene[i])) return 1;
    }
    return 0;
}

static void ff_log_buffer_once(const char *kind, const char *name, uint32_t ow, uint32_t oh,
                               uint32_t nw, uint32_t nh, const char *tag) {
    typedef struct { char kind[8], name[48]; uint32_t ow, oh, nw, nh; } Entry;
    static Entry seen[64];
    static int n_seen = 0;
    static PlatMutex lock = PLAT_MUTEX_INIT;
    plat_mutex_lock(&lock);
    for (int i = 0; i < n_seen; i++) {
        if (seen[i].ow == ow && seen[i].oh == oh && seen[i].nw == nw && seen[i].nh == nh &&
            !strcmp(seen[i].kind, kind) && !strcmp(seen[i].name, name)) {
            plat_mutex_unlock(&lock);
            return;
        }
    }
    int emit = (n_seen < (int)(sizeof seen / sizeof *seen));
    if (emit) {
        Entry *e = &seen[n_seen++];
        snprintf(e->kind, sizeof e->kind, "%s", kind);
        snprintf(e->name, sizeof e->name, "%s", name);
        e->ow = ow; e->oh = oh; e->nw = nw; e->nh = nh;
    }
    plat_mutex_unlock(&lock);
    if (emit) rt_log("ff: %sBuffer '%s': %ux%u -> %ux%u (%s)", kind, name, ow, oh, nw, nh, tag);
}

static void ff_maybe_lift_buffer(uint64_t desc, uint32_t name_off, const char *kind) {
    if (!desc || !plat_mem_readable(desc, name_off + 8u)) return;
    uint32_t w = LD32(desc);
    uint32_t h = LD32(desc + 4u);
    uint64_t name_ptr = LD64(desc + name_off);
    char name[64];
    ff_read_guest_cstr(name_ptr, name, sizeof name);
    int is_16_9 = (w >= 1280u && w <= 7680u && h >= 720u && (uint64_t)w * 9u == (uint64_t)h * 16u);
    if (!is_16_9) return;
    int is_canvas = ff_is_canvas_buffer(name);
    int is_scene = !is_canvas && ff_is_scene_target(name);
    if (!name[0] || (!is_canvas && !is_scene && ff_is_aux_buffer(name))) {
        ff_log_buffer_once(kind, name[0] ? name : "<unnamed>", w, h, w, h, "aux/skip");
        return;
    }
    uint32_t cw = 0, ch = 0, sw = 0, sh = 0;
    ff_get_target_resolutions(&cw, &ch, &sw, &sh);
    uint32_t tw = is_canvas ? cw : sw;
    uint32_t th = is_canvas ? ch : sh;
    ST32(desc, tw);
    ST32(desc + 4u, th);
    ff_log_buffer_once(kind, name, w, h, tw, th, is_canvas ? "canvas" : "scene");
}

static void ff_log_raw_buffer(uint64_t desc, uint32_t name_off, const char *kind) {
    if (!desc || !plat_mem_readable(desc, name_off + 8u)) return;
    uint32_t w = LD32(desc), h = LD32(desc + 4u);
    if (w < 640u || h < 360u || (uint64_t)w * 9u != (uint64_t)h * 16u) return;
    char name[64];
    ff_read_guest_cstr(LD64(desc + name_off), name, sizeof name);
    ff_log_buffer_once(kind, name[0] ? name : "<unnamed>", w, h, w, h, "raw/nohook");
}

void __wrap_f_70001080b0(Cpu *c) {
    if (res_ff_enabled()) ff_maybe_lift_buffer(c->x[0], 0x40u, "Color");
    else ff_log_raw_buffer(c->x[0], 0x40u, "Color");
    ff_call_real(__real_f_70001080b0, 0x70001080b0ull, c);
}

void __wrap_f_7000108170(Cpu *c) {
    if (res_ff_enabled()) ff_maybe_lift_buffer(c->x[0], 0x20u, "Depth");
    else ff_log_raw_buffer(c->x[0], 0x20u, "Depth");
    ff_call_real(__real_f_7000108170, 0x7000108170ull, c);
}

void __wrap_f_700002c764(Cpu *c) {
    if (c->x[2] && plat_mem_readable(c->x[2] + 0x2bcull, 12u)) {
        float old_scale = 0.0f, old_w = 0.0f, old_h = 0.0f;
        uint32_t bs = LD32(c->x[2] + 0x2bcull);
        uint32_t bw = LD32(c->x[2] + 0x2c0ull);
        uint32_t bh = LD32(c->x[2] + 0x2c4ull);
        memcpy(&old_scale, &bs, 4);
        memcpy(&old_w, &bw, 4);
        memcpy(&old_h, &bh, 4);
        if (res_ff_enabled()) {
            uint32_t cw = 0, ch = 0;
            ff_get_target_resolutions(&cw, &ch, NULL, NULL);
            float new_scale = 1.0f, fw = (float)cw, fh = (float)ch;
            uint32_t us = 0, uw = 0, uh = 0;
            memcpy(&us, &new_scale, 4);
            memcpy(&uw, &fw, 4);
            memcpy(&uh, &fh, 4);
            ST32(c->x[2] + 0x2bcull, us);
            ST32(c->x[2] + 0x2c0ull, uw);
            ST32(c->x[2] + 0x2c4ull, uh);
            if (old_scale != 1.0f || old_w != fw || old_h != fh) {
                static int logged = 0;
                if (!logged) {
                    logged = 1;
                    rt_log("ff: C3dFxDlss scale=%.3f->1.000 out=%.0fx%.0f->%ux%u",
                           old_scale, old_w, old_h, cw, ch);
                }
            }
        } else {
            static int logged = 0;
            if (!logged) {
                logged = 1;
                rt_log("ff: C3dFxDlss (nohook) scale=%.3f out=%.0fx%.0f", old_scale, old_w, old_h);
            }
        }
    }
    ff_call_real(__real_f_700002c764, 0x700002c764ull, c);
}
