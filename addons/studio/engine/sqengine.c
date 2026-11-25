/*
 * sqengine: the Studio's render engine, in plain C.
 *
 * It draws a motion.json video (converted by addons/studio/engine.py into a simple "scene" text
 * file) frame by frame, with the same rules as the editor's browser renderer (render.js), and:
 *
 *   sqengine video SCENE [--threads N] [--mem MB]      raw RGBA frames on stdout, for ffmpeg to encode
 *   sqengine still SCENE OUT.png --times a,b,c [--scale S] [--cols C] [--threads N] [--mem MB]
 *   sqengine audio SCENE                               mixes the audio clips into the WAV the scene names
 *   sqengine info                                      version and CPU count
 *
 * Everything heavy runs on the machine that serves the editor, not in the browser: a phone can
 * start an export and go to sleep. Decoding and encoding go through the ffmpeg program (pipes),
 * so the engine needs nothing but a C compiler and builds the same on Windows, macOS and Linux.
 *
 * CPU: frames are split into horizontal bands rendered by a pool of worker threads (--threads).
 * Memory: --mem caps what the engine allocates; it lowers the thread count to fit, and refuses
 * with a clear message when even one thread would not fit. GPU: hardware decode/encode is chosen
 * by the Python side (ffmpeg arguments); the engine itself only uses the CPU.
 *
 * Third-party: stb_truetype, stb_image, stb_image_write (public domain / MIT), in vendor/.
 */

#define _CRT_SECURE_NO_WARNINGS
#if !defined(_WIN32) && !defined(__APPLE__) && !defined(_POSIX_C_SOURCE)
#define _POSIX_C_SOURCE 200809L   /* popen, sysconf */
#endif
#include <ctype.h>
#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#include <windows.h>
#define popen _popen
#define pclose _pclose
#define PIPE_READ "rb" /* binary pipe on Windows */
#else
#include <pthread.h>
#include <unistd.h>
#define PIPE_READ "r"  /* glibc refuses "rb"; POSIX pipes are binary anyway */
#endif

#define STB_TRUETYPE_IMPLEMENTATION
#include "vendor/stb_truetype.h"
#define STB_IMAGE_IMPLEMENTATION
#define STBI_NO_GIF
#define STBI_NO_PSD
#define STBI_NO_PIC
#define STBI_NO_PNM
#define STBI_NO_HDR
#include "vendor/stb_image.h"
#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "vendor/stb_image_write.h"

#define SQ_VERSION "1.1.0"
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* ── small helpers ─────────────────────────────────────────────────────────── */

static void die(int code, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fputs("sqengine: ", stderr);
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
    exit(code);
}

static void *xmalloc(size_t n) {
    void *p = malloc(n ? n : 1);
    if (!p) die(3, "out of memory (%zu bytes)", n);
    return p;
}

static void *xcalloc(size_t n, size_t size) {
    void *p = calloc(n ? n : 1, size ? size : 1);
    if (!p) die(3, "out of memory (%zu bytes)", n * size);
    return p;
}

static void *xrealloc(void *p, size_t n) {
    void *q = realloc(p, n ? n : 1);
    if (!q) die(3, "out of memory (%zu bytes)", n);
    return q;
}

static char *xstrdup(const char *s) {
    size_t n = strlen(s) + 1;
    char *d = xmalloc(n);
    memcpy(d, s, n);
    return d;
}

static float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }

/* %XX decoding: the scene escapes %, tab, CR and LF in strings. */
static char *pct_decode(const char *s) {
    size_t n = strlen(s);
    char *out = xmalloc(n + 1), *o = out;
    for (size_t i = 0; i < n; i++) {
        if (s[i] == '%' && i + 2 < n) {
            char hex[3] = {s[i + 1], s[i + 2], 0};
            *o++ = (char)strtol(hex, NULL, 16);
            i += 2;
        } else {
            *o++ = s[i];
        }
    }
    *o = 0;
    return out;
}

/* Split a line on tabs in place. Returns the number of fields. */
static int split_tabs(char *line, char **fields, int max) {
    int n = 0;
    char *p = line;
    while (n < max) {
        fields[n++] = p;
        char *tab = strchr(p, '\t');
        if (!tab) break;
        *tab = 0;
        p = tab + 1;
    }
    return n;
}

/* ── easing and keyframes (same as render.js) ───────────────────────────────── */

enum { E_LINEAR, E_IN, E_OUT, E_INOUT, E_OUTBACK, E_OUTELASTIC, E_HOLD, E_INBACK, E_OUTBOUNCE, E_BEZIER };

/* An ease: a named curve, or cubic-bezier(x1,y1,x2,y2) drawn in the editor's curve editor. */
typedef struct { int id; float x1, y1, x2, y2; } Ease;

static Ease ease_parse(const char *s) {
    Ease e = {E_LINEAR, 0, 0, 1, 1};
    if (!strncmp(s, "cubic-bezier(", 13)) {
        if (sscanf(s + 13, "%f ,%f ,%f ,%f", &e.x1, &e.y1, &e.x2, &e.y2) == 4) e.id = E_BEZIER;
        return e;
    }
    if (!strcmp(s, "in")) e.id = E_IN;
    else if (!strcmp(s, "out")) e.id = E_OUT;
    else if (!strcmp(s, "inOut")) e.id = E_INOUT;
    else if (!strcmp(s, "outBack")) e.id = E_OUTBACK;
    else if (!strcmp(s, "outElastic")) e.id = E_OUTELASTIC;
    else if (!strcmp(s, "hold")) e.id = E_HOLD;
    else if (!strcmp(s, "inBack")) e.id = E_INBACK;
    else if (!strcmp(s, "outBounce")) e.id = E_OUTBOUNCE;
    return e;
}

/* CSS cubic-bezier: x(t) = p, solved by Newton steps, bisection when those do not settle (as render.js). */
static double bez_at(double t, double p1, double p2) { double u = 1 - t; return 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t; }
static double bez_slope(double t, double p1, double p2) { double u = 1 - t; return 3 * u * u * p1 + 6 * u * t * (p2 - p1) + 3 * t * t * (1 - p2); }

static double bezier(const Ease *e, double x) {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    double t = x;
    for (int i = 0; i < 8; i++) {
        double err = bez_at(t, e->x1, e->x2) - x;
        if (fabs(err) < 1e-7 && t >= 0 && t <= 1) return bez_at(t, e->y1, e->y2);
        double d = bez_slope(t, e->x1, e->x2);
        if (fabs(d) < 1e-6) break;
        t -= err / d;
    }
    double lo = 0, hi = 1;
    t = x;
    for (int i = 0; i < 60; i++) {
        double v = bez_at(t, e->x1, e->x2);
        if (fabs(v - x) < 1e-7) break;
        if (v < x) lo = t; else hi = t;
        t = (lo + hi) / 2;
    }
    return bez_at(t, e->y1, e->y2);
}

static double ease(const Ease *e, double p) {
    switch (e->id) {
    case E_IN: return p * p * p;
    case E_OUT: return 1 - pow(1 - p, 3);
    case E_INOUT: return p < 0.5 ? 4 * p * p * p : 1 - pow(-2 * p + 2, 3) / 2;
    case E_OUTBACK: { double c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * pow(p - 1, 3) + c1 * pow(p - 1, 2); }
    case E_OUTELASTIC:
        if (p == 0 || p == 1) return p;
        return pow(2, -10 * p) * sin((p * 10 - 0.75) * (2 * M_PI / 3)) + 1;
    case E_HOLD: return 0;
    case E_INBACK: { double c1 = 1.70158, c3 = c1 + 1; return c3 * p * p * p - c1 * p * p; }
    case E_OUTBOUNCE: {
        const double n1 = 7.5625, d1 = 2.75;
        if (p < 1 / d1) return n1 * p * p;
        if (p < 2 / d1) { p -= 1.5 / d1; return n1 * p * p + 0.75; }
        if (p < 2.5 / d1) { p -= 2.25 / d1; return n1 * p * p + 0.9375; }
        p -= 2.625 / d1;
        return n1 * p * p + 0.984375;
    }
    case E_BEZIER: return bezier(e, p);
    default: return p;
    }
}

typedef struct { float t; float v; float c[4]; int has_color; Ease ease; } Key;
typedef struct { char name[16]; Key *k; int n, cap; int color; } Track;

static void track_add(Track *tr, Key key) {
    if (tr->n == tr->cap) { tr->cap = tr->cap ? tr->cap * 2 : 8; tr->k = xrealloc(tr->k, sizeof(Key) * (size_t)tr->cap); }
    tr->k[tr->n++] = key;
}

static float track_num(const Track *tr, double t, float fallback) {
    if (!tr || tr->n == 0) return fallback;
    if (t <= tr->k[0].t) return tr->k[0].v;
    const Key *last = &tr->k[tr->n - 1];
    if (t >= last->t) return last->v;
    for (int i = 0; i < tr->n - 1; i++) {
        const Key *a = &tr->k[i], *b = &tr->k[i + 1];
        if (t >= a->t && t < b->t) {
            double span = b->t - a->t;
            if (span == 0) span = 1;
            double p = ease(&a->ease, (t - a->t) / span);
            return (float)(a->v + (b->v - a->v) * p);
        }
    }
    return last->v;
}

/* A colour at time t; has=0 means no colour (null). */
static void track_color(const Track *tr, double t, const float *fallback, int fallback_has, float *out, int *has) {
    if (!tr || tr->n == 0) { memcpy(out, fallback, sizeof(float) * 4); *has = fallback_has; return; }
    const Key *k0 = &tr->k[0], *last = &tr->k[tr->n - 1];
    const Key *pick = NULL;
    if (t <= k0->t) pick = k0;
    else if (t >= last->t) pick = last;
    if (pick) { memcpy(out, pick->c, sizeof(float) * 4); *has = pick->has_color; return; }
    for (int i = 0; i < tr->n - 1; i++) {
        const Key *a = &tr->k[i], *b = &tr->k[i + 1];
        if (t >= a->t && t < b->t) {
            double span = b->t - a->t;
            if (span == 0) span = 1;
            double p = ease(&a->ease, (t - a->t) / span);
            if (!a->has_color || !b->has_color) {
                const Key *k = p < 0.5 ? a : b;
                memcpy(out, k->c, sizeof(float) * 4);
                *has = k->has_color;
                return;
            }
            for (int c = 0; c < 4; c++) out[c] = (float)(a->c[c] + (b->c[c] - a->c[c]) * p);
            *has = 1;
            return;
        }
    }
    memcpy(out, last->c, sizeof(float) * 4);
    *has = last->has_color;
}

/* ── scene ─────────────────────────────────────────────────────────────────── */

typedef struct { char *id; unsigned char *data; stbtt_fontinfo info; int ok; } Font;

typedef struct {
    int w, h;
    unsigned char *px; /* RGBA8, straight alpha */
} Level;

typedef struct { char *id; Level lv[16]; int levels; } Image;

typedef struct {
    char *id, *cmd;
    int w, h;
    double t0;
    FILE *pipe;
    long next;          /* index of the next frame the pipe will give */
    long have;          /* index of the frame in buf, -1 = none */
    int ended;
    unsigned char *buf; /* RGBA8 */
} Video;

enum { L_TEXT, L_RECT, L_ELLIPSE, L_POLYGON, L_PATH, L_IMAGE, L_VIDEO, L_OTHER };

enum {
    P_X, P_Y, P_SCALE, P_SCALEX, P_SCALEY, P_ROT, P_OPACITY, P_BLUR, P_DRAW, P_W, P_H, P_RADIUS, P_SIZE, P_STROKEW,
    P_BRIGHT, P_CONTRAST, P_SAT, P_HUE, P_TEMP,
    P_COUNT
};
static const char *PROP_NAMES[P_COUNT] = {"x", "y", "scale", "scaleX", "scaleY", "rotation", "opacity", "blur", "draw",
                                          "w", "h", "radius", "size", "strokeWidth",
                                          "brightness", "contrast", "saturation", "hue", "temperature"};
static const float PROP_DEFAULTS[P_COUNT] = {0, 0, 1, 1, 1, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0};

/* Transitions: how a layer comes in at its start and leaves at its end. */
enum { T_NONE, T_FADE, T_SLIDE_L, T_SLIDE_R, T_SLIDE_U, T_SLIDE_D, T_ZOOM, T_BLUR, T_WIPE_L, T_WIPE_R, T_WIPE_U, T_WIPE_D, T_IRIS };
static const char *TRANS_NAMES[] = {"none", "fade", "slide-left", "slide-right", "slide-up", "slide-down", "zoom", "blur",
                                    "wipe-left", "wipe-right", "wipe-up", "wipe-down", "iris"};
typedef struct { int type; float dur; Ease ease; } Trans;

static int trans_id(const char *s) {
    for (int i = 0; i < (int)(sizeof(TRANS_NAMES) / sizeof(TRANS_NAMES[0])); i++) if (!strcmp(TRANS_NAMES[i], s)) return i;
    return T_NONE;
}

/* A 3D colour lookup table from a .cube file (r fastest), sampled trilinearly. */
typedef struct Lut { char *id; int n; float *d; float dmin[3], dmax[3]; } Lut;

typedef struct {
    int type;
    double start, end;
    float stat[P_COUNT];
    Track *num_keys[P_COUNT];
    float fill[4], stroke[4];
    int has_fill, has_stroke;
    Track *fill_keys, *stroke_keys;
    char *text;
    Font *font;
    int align; /* 0 left 1 center 2 right */
    float line_height, width;
    int sides, star;
    float inner;
    char *d;
    Image *image;
    Video *video;
    int cover;
    /* effects */
    Trans tin, tout;
    int has_chroma; float kcb, kcr, ksim, ksoft, kspill;
    Lut *lut; float lut_mix;
    int has_mask, mask_ellipse, mask_invert; float mask_x, mask_y, mask_w, mask_h, mask_r, mask_feather;
} Layer;

typedef struct {
    int w, h;
    int out_w, out_h; /* output size (0 = same as the video's) */
    double fps, duration;
    float bg[4];
    Font *fonts; int nfonts;
    Image *images; int nimages;
    Video *videos; int nvideos;
    Lut *luts; int nluts;
    Layer *layers; int nlayers;
    /* audio */
    char *audio_out; int audio_rate;
    struct AClip *clips; int nclips;
} Scene;

typedef struct AClip {
    char *cmd;
    double start, length, volume, fade_in, fade_out;
    Track keys;
} AClip;

static int parse_prop(const char *name) {
    for (int i = 0; i < P_COUNT; i++) if (!strcmp(PROP_NAMES[i], name)) return i;
    return -1;
}

static Font *find_font(Scene *s, const char *id) {
    for (int i = 0; i < s->nfonts; i++) if (!strcmp(s->fonts[i].id, id)) return &s->fonts[i];
    return NULL;
}
static Image *find_image(Scene *s, const char *id) {
    for (int i = 0; i < s->nimages; i++) if (!strcmp(s->images[i].id, id)) return &s->images[i];
    return NULL;
}
static Video *find_video(Scene *s, const char *id) {
    for (int i = 0; i < s->nvideos; i++) if (!strcmp(s->videos[i].id, id)) return &s->videos[i];
    return NULL;
}
static Lut *find_lut(Scene *s, const char *id) {
    for (int i = 0; i < s->nluts; i++) if (!strcmp(s->luts[i].id, id)) return &s->luts[i];
    return NULL;
}

/* Read a .cube 3D LUT. 0 when the file is missing, 1D only, or short. */
static int lut_load(Lut *lt, const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    char line[1024];
    long count = 0, total = 0;
    lt->dmin[0] = lt->dmin[1] = lt->dmin[2] = 0;
    lt->dmax[0] = lt->dmax[1] = lt->dmax[2] = 1;
    while (fgets(line, sizeof(line), f)) {
        char *s = line;
        while (*s && isspace((unsigned char)*s)) s++;
        if (!*s || *s == '#') continue;
        if (!strncmp(s, "LUT_3D_SIZE", 11)) {
            int n = atoi(s + 11);
            if (n < 2 || n > 256 || lt->d) break;
            lt->n = n;
            total = (long)n * n * n;
            lt->d = xmalloc(sizeof(float) * 3 * (size_t)total);
            continue;
        }
        if (!strncmp(s, "DOMAIN_MIN", 10)) { sscanf(s + 10, "%f %f %f", &lt->dmin[0], &lt->dmin[1], &lt->dmin[2]); continue; }
        if (!strncmp(s, "DOMAIN_MAX", 10)) { sscanf(s + 10, "%f %f %f", &lt->dmax[0], &lt->dmax[1], &lt->dmax[2]); continue; }
        if (isalpha((unsigned char)*s)) continue; /* TITLE, LUT_1D_SIZE (not supported: no table, so refused below) */
        if (!lt->d || count >= total) continue;
        float r, g, b;
        if (sscanf(s, "%f %f %f", &r, &g, &b) == 3) { lt->d[count * 3] = r; lt->d[count * 3 + 1] = g; lt->d[count * 3 + 2] = b; count++; }
    }
    fclose(f);
    if (!lt->d || count != total) { free(lt->d); lt->d = NULL; return 0; }
    return 1;
}

static void lut_sample(const Lut *lt, const float *in, float *out) {
    int n = lt->n, i0[3];
    float fr[3];
    for (int k = 0; k < 3; k++) {
        float span = lt->dmax[k] - lt->dmin[k];
        float c = span > 0 ? (in[k] - lt->dmin[k]) / span : in[k];
        c = clampf(c, 0, 1) * (float)(n - 1);
        int i = (int)floorf(c);
        if (i > n - 2) i = n - 2;
        i0[k] = i;
        fr[k] = c - (float)i;
    }
    out[0] = out[1] = out[2] = 0;
    for (int corner = 0; corner < 8; corner++) {
        int dr = corner & 1, dg = (corner >> 1) & 1, db = (corner >> 2) & 1;
        float w = (dr ? fr[0] : 1 - fr[0]) * (dg ? fr[1] : 1 - fr[1]) * (db ? fr[2] : 1 - fr[2]);
        if (w == 0) continue;
        const float *p = lt->d + 3 * ((size_t)(i0[0] + dr) + (size_t)(i0[1] + dg) * n + (size_t)(i0[2] + db) * n * n);
        out[0] += p[0] * w; out[1] += p[1] * w; out[2] += p[2] * w;
    }
}

static unsigned char *read_file(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n < 0) { fclose(f); return NULL; }
    unsigned char *buf = xmalloc((size_t)n + 1);
    size_t got = fread(buf, 1, (size_t)n, f);
    fclose(f);
    if (size) *size = got;
    return buf;
}

/* Halve an RGBA8 level (box filter), for clean downscaling of large pictures. */
static void build_mips(Image *im) {
    while (im->levels < 16) {
        Level *a = &im->lv[im->levels - 1];
        if (a->w <= 1 && a->h <= 1) break;
        int w = a->w > 1 ? a->w / 2 : 1, h = a->h > 1 ? a->h / 2 : 1;
        Level *b = &im->lv[im->levels];
        b->w = w; b->h = h;
        b->px = xmalloc((size_t)w * h * 4);
        for (int y = 0; y < h; y++)
            for (int x = 0; x < w; x++)
                for (int c = 0; c < 4; c++) {
                    int x0 = x * 2 < a->w ? x * 2 : a->w - 1, x1 = x * 2 + 1 < a->w ? x * 2 + 1 : a->w - 1;
                    int y0 = y * 2 < a->h ? y * 2 : a->h - 1, y1 = y * 2 + 1 < a->h ? y * 2 + 1 : a->h - 1;
                    int sum = a->px[((size_t)y0 * a->w + x0) * 4 + c] + a->px[((size_t)y0 * a->w + x1) * 4 + c] +
                              a->px[((size_t)y1 * a->w + x0) * 4 + c] + a->px[((size_t)y1 * a->w + x1) * 4 + c];
                    b->px[((size_t)y * w + x) * 4 + c] = (unsigned char)((sum + 2) / 4);
                }
        im->levels++;
        if (w < 64 && h < 64) break;
    }
}

static Track *new_track(const char *name, int color) {
    Track *t = xcalloc(1, sizeof(Track));
    strncpy(t->name, name, sizeof(t->name) - 1);
    t->color = color;
    return t;
}

static void parse_color(char **f, int n, int at, float *out, int *has) {
    if (n <= at || !strcmp(f[at], "none")) { out[0] = out[1] = out[2] = out[3] = 0; *has = 0; return; }
    for (int c = 0; c < 4; c++) out[c] = n > at + c ? (float)atof(f[at + c]) : 1.0f;
    *has = 1;
}

static void load_scene(Scene *s, const char *path) {
    memset(s, 0, sizeof(*s));
    FILE *f = fopen(path, "rb");
    if (!f) die(2, "cannot open scene %s", path);
    size_t cap = 1 << 16;
    char *line = xmalloc(cap);
    Layer *cur = NULL;
    AClip *clip = NULL;
    int seen_header = 0;
    for (;;) {
        /* read one line of any length */
        size_t len = 0;
        int ch;
        while ((ch = fgetc(f)) != EOF && ch != '\n') {
            if (len + 2 >= cap) { cap *= 2; line = xrealloc(line, cap); }
            line[len++] = (char)ch;
        }
        if (ch == EOF && len == 0) break;
        if (len && line[len - 1] == '\r') len--;
        line[len] = 0;
        char *fld[16];
        int n = split_tabs(line, fld, 16);
        const char *k = fld[0];
        if (!*k) continue;
        if (!strcmp(k, "SQSCENE")) { seen_header = 1; continue; }
        if (!seen_header) die(2, "not a scene file (no SQSCENE header)");
        if (!strcmp(k, "canvas") && n >= 5) {
            s->w = atoi(fld[1]); s->h = atoi(fld[2]); s->fps = atof(fld[3]); s->duration = atof(fld[4]);
        } else if (!strcmp(k, "output") && n >= 3) {
            s->out_w = atoi(fld[1]); s->out_h = atoi(fld[2]);
        } else if (!strcmp(k, "background") && n >= 5) {
            for (int c = 0; c < 4; c++) s->bg[c] = (float)atof(fld[1 + c]);
        } else if (!strcmp(k, "font") && n >= 3 && !cur) {
            s->fonts = xrealloc(s->fonts, sizeof(Font) * (size_t)(s->nfonts + 1));
            Font *ft = &s->fonts[s->nfonts++];
            memset(ft, 0, sizeof(*ft));
            ft->id = xstrdup(fld[1]);
            char *p = pct_decode(fld[2]);
            ft->data = read_file(p, NULL);
            ft->ok = ft->data && stbtt_InitFont(&ft->info, ft->data, stbtt_GetFontOffsetForIndex(ft->data, 0));
            if (!ft->ok) fprintf(stderr, "sqengine: font %s could not be read (%s)\n", ft->id, p);
            free(p);
        } else if (!strcmp(k, "image") && n >= 3) {
            s->images = xrealloc(s->images, sizeof(Image) * (size_t)(s->nimages + 1));
            Image *im = &s->images[s->nimages++];
            memset(im, 0, sizeof(*im));
            im->id = xstrdup(fld[1]);
            char *p = pct_decode(fld[2]);
            int w, h, comp;
            unsigned char *px = stbi_load(p, &w, &h, &comp, 4);
            if (px) { im->lv[0].w = w; im->lv[0].h = h; im->lv[0].px = px; im->levels = 1; build_mips(im); }
            else fprintf(stderr, "sqengine: image %s could not be read (%s)\n", im->id, p);
            free(p);
        } else if (!strcmp(k, "video") && n >= 6) {
            s->videos = xrealloc(s->videos, sizeof(Video) * (size_t)(s->nvideos + 1));
            Video *v = &s->videos[s->nvideos++];
            memset(v, 0, sizeof(*v));
            v->id = xstrdup(fld[1]);
            v->w = atoi(fld[2]); v->h = atoi(fld[3]); v->t0 = atof(fld[4]);
            v->cmd = pct_decode(fld[5]);
            v->have = -1;
        } else if (!strcmp(k, "layer") && n >= 4) {
            s->layers = xrealloc(s->layers, sizeof(Layer) * (size_t)(s->nlayers + 1));
            cur = &s->layers[s->nlayers++];
            memset(cur, 0, sizeof(*cur));
            const char *t = fld[1];
            cur->type = !strcmp(t, "text") ? L_TEXT : !strcmp(t, "rect") ? L_RECT : !strcmp(t, "ellipse") ? L_ELLIPSE
                      : !strcmp(t, "polygon") ? L_POLYGON : !strcmp(t, "path") ? L_PATH : !strcmp(t, "image") ? L_IMAGE
                      : !strcmp(t, "video") ? L_VIDEO : L_OTHER;
            cur->start = atof(fld[2]);
            cur->end = !strcmp(fld[3], "inf") ? 1e18 : atof(fld[3]);
            memcpy(cur->stat, PROP_DEFAULTS, sizeof(cur->stat));
            cur->align = 1; cur->line_height = 1.15f; cur->sides = 5; cur->inner = 0.5f;
        } else if (!strcmp(k, "endlayer")) {
            cur = NULL;
        } else if (cur && !strcmp(k, "num") && n >= 3) {
            int p = parse_prop(fld[1]);
            if (p >= 0) cur->stat[p] = (float)atof(fld[2]);
        } else if (cur && !strcmp(k, "col") && n >= 3) {
            if (!strcmp(fld[1], "fill")) parse_color(fld, n, 2, cur->fill, &cur->has_fill);
            else if (!strcmp(fld[1], "stroke")) parse_color(fld, n, 2, cur->stroke, &cur->has_stroke);
        } else if (cur && !strcmp(k, "key") && n >= 5) {
            int p = parse_prop(fld[1]);
            if (p < 0) continue;
            if (!cur->num_keys[p]) cur->num_keys[p] = new_track(fld[1], 0);
            Key key = {(float)atof(fld[2]), (float)atof(fld[3]), {0}, 0, ease_parse(fld[4])};
            track_add(cur->num_keys[p], key);
        } else if (cur && !strcmp(k, "ckey") && n >= 5) {
            Track **tr = !strcmp(fld[1], "fill") ? &cur->fill_keys : !strcmp(fld[1], "stroke") ? &cur->stroke_keys : NULL;
            if (!tr) continue;
            if (!*tr) *tr = new_track(fld[1], 1);
            Key key = {(float)atof(fld[2]), 0, {0}, 0, ease_parse(fld[3])};
            parse_color(fld, n, 4, key.c, &key.has_color);
            track_add(*tr, key);
        } else if (cur && !strcmp(k, "text") && n >= 2) {
            cur->text = pct_decode(fld[1]);
        } else if (cur && !strcmp(k, "font") && n >= 2) {
            cur->font = find_font(s, fld[1]);
        } else if (cur && !strcmp(k, "align") && n >= 2) {
            cur->align = !strcmp(fld[1], "left") ? 0 : !strcmp(fld[1], "right") ? 2 : 1;
        } else if (cur && !strcmp(k, "lineh") && n >= 2) {
            cur->line_height = (float)atof(fld[1]);
        } else if (cur && !strcmp(k, "width") && n >= 2) {
            cur->width = (float)atof(fld[1]);
        } else if (cur && !strcmp(k, "poly") && n >= 4) {
            cur->sides = atoi(fld[1]); cur->star = atoi(fld[2]); cur->inner = (float)atof(fld[3]);
        } else if (cur && !strcmp(k, "d") && n >= 2) {
            cur->d = pct_decode(fld[1]);
        } else if (cur && !strcmp(k, "asset") && n >= 2) {
            cur->image = find_image(s, fld[1]);
            cur->video = find_video(s, fld[1]);
        } else if (cur && !strcmp(k, "fit") && n >= 2) {
            cur->cover = !strcmp(fld[1], "cover");
        } else if (!strcmp(k, "lutfile") && n >= 3) {
            s->luts = xrealloc(s->luts, sizeof(Lut) * (size_t)(s->nluts + 1));
            Lut *lt = &s->luts[s->nluts++];
            memset(lt, 0, sizeof(*lt));
            lt->id = xstrdup(fld[1]);
            char *p = pct_decode(fld[2]);
            if (!lut_load(lt, p)) fprintf(stderr, "sqengine: LUT %s could not be read (%s; a 3D .cube is needed)\n", lt->id, p);
            free(p);
        } else if (cur && !strcmp(k, "lut") && n >= 3) {
            cur->lut = find_lut(s, fld[1]);
            if (cur->lut && !cur->lut->d) cur->lut = NULL;
            cur->lut_mix = clampf((float)atof(fld[2]), 0, 1);
        } else if (cur && !strcmp(k, "trans") && n >= 5) {
            Trans *tr = !strcmp(fld[1], "out") ? &cur->tout : &cur->tin;
            tr->type = trans_id(fld[2]); tr->dur = (float)atof(fld[3]); tr->ease = ease_parse(fld[4]);
        } else if (cur && !strcmp(k, "chroma") && n >= 7) {
            float r = (float)atof(fld[1]), g = (float)atof(fld[2]), b = (float)atof(fld[3]);
            float y = 0.299f * r + 0.587f * g + 0.114f * b;
            cur->has_chroma = 1;
            cur->kcb = (b - y) * 0.564f; cur->kcr = (r - y) * 0.713f;
            cur->ksim = (float)atof(fld[4]); cur->ksoft = (float)atof(fld[5]); cur->kspill = (float)atof(fld[6]);
        } else if (cur && !strcmp(k, "mask") && n >= 9) {
            cur->has_mask = 1;
            cur->mask_ellipse = !strcmp(fld[1], "ellipse");
            cur->mask_x = (float)atof(fld[2]); cur->mask_y = (float)atof(fld[3]);
            cur->mask_w = (float)atof(fld[4]); cur->mask_h = (float)atof(fld[5]);
            cur->mask_r = (float)atof(fld[6]); cur->mask_feather = (float)atof(fld[7]);
            cur->mask_invert = atoi(fld[8]);
        } else if (!strcmp(k, "audio") && n >= 4) {
            s->audio_out = pct_decode(fld[1]);
            s->audio_rate = atoi(fld[2]);
            if (s->duration <= 0) s->duration = atof(fld[3]);
        } else if (!strcmp(k, "aclip") && n >= 7) {
            s->clips = xrealloc(s->clips, sizeof(AClip) * (size_t)(s->nclips + 1));
            clip = &s->clips[s->nclips++];
            memset(clip, 0, sizeof(*clip));
            clip->cmd = pct_decode(fld[1]);
            clip->start = atof(fld[2]); clip->length = atof(fld[3]); clip->volume = atof(fld[4]);
            clip->fade_in = atof(fld[5]); clip->fade_out = atof(fld[6]);
        } else if (clip && !strcmp(k, "akey") && n >= 4) {
            Key key = {(float)atof(fld[1]), (float)atof(fld[2]), {0}, 0, ease_parse(fld[3])};
            track_add(&clip->keys, key);
        }
    }
    free(line);
    fclose(f);
    if (s->w <= 0 || s->h <= 0 || s->fps <= 0) {
        if (!s->audio_out) die(2, "scene has no canvas line");
    }
}

/* ── geometry ──────────────────────────────────────────────────────────────── */

typedef struct { float x, y; } Pt;
typedef struct { Pt *p; int n, cap; int closed; } Poly;
typedef struct { Poly *v; int n, cap; } Shape;
typedef struct { double a, b, c, d, e, f; } Mat; /* x' = a x + c y + e ; y' = b x + d y + f */

static Pt mat_apply(const Mat *m, float x, float y) {
    Pt r = {(float)(m->a * x + m->c * y + m->e), (float)(m->b * x + m->d * y + m->f)};
    return r;
}

static Mat mat_mul(Mat m, Mat n) { /* m then n applied first: result = m * n */
    Mat r = {m.a * n.a + m.c * n.b, m.b * n.a + m.d * n.b, m.a * n.c + m.c * n.d, m.b * n.c + m.d * n.d,
             m.a * n.e + m.c * n.f + m.e, m.b * n.e + m.d * n.f + m.f};
    return r;
}

static int mat_invert(const Mat *m, Mat *out) {
    double det = m->a * m->d - m->b * m->c;
    if (fabs(det) < 1e-12) return 0;
    double id = 1.0 / det;
    out->a = m->d * id; out->b = -m->b * id; out->c = -m->c * id; out->d = m->a * id;
    out->e = -(out->a * m->e + out->c * m->f);
    out->f = -(out->b * m->e + out->d * m->f);
    return 1;
}

static double mat_scale(const Mat *m) { return sqrt(fabs(m->a * m->d - m->b * m->c)); }

static void poly_add(Poly *pl, float x, float y) {
    if (pl->n && fabsf(pl->p[pl->n - 1].x - x) < 1e-6f && fabsf(pl->p[pl->n - 1].y - y) < 1e-6f) return;
    if (pl->n == pl->cap) { pl->cap = pl->cap ? pl->cap * 2 : 16; pl->p = xrealloc(pl->p, sizeof(Pt) * (size_t)pl->cap); }
    pl->p[pl->n].x = x; pl->p[pl->n].y = y; pl->n++;
}

static Poly *shape_new_poly(Shape *s) {
    if (s->n == s->cap) { s->cap = s->cap ? s->cap * 2 : 8; s->v = xrealloc(s->v, sizeof(Poly) * (size_t)s->cap); }
    Poly *p = &s->v[s->n++];
    memset(p, 0, sizeof(*p));
    return p;
}

static void shape_free(Shape *s) {
    for (int i = 0; i < s->n; i++) free(s->v[i].p);
    free(s->v);
    memset(s, 0, sizeof(*s));
}

/* Curves become line segments: about one segment per `tol` units of control-polygon length. */
static void flat_quad(Poly *pl, float x0, float y0, float cx, float cy, float x1, float y1, float tol) {
    float len = hypotf(cx - x0, cy - y0) + hypotf(x1 - cx, y1 - cy);
    int steps = (int)ceilf(len / tol);
    if (steps < 2) steps = 2;
    if (steps > 256) steps = 256;
    for (int i = 1; i <= steps; i++) {
        float t = (float)i / steps, u = 1 - t;
        poly_add(pl, u * u * x0 + 2 * u * t * cx + t * t * x1, u * u * y0 + 2 * u * t * cy + t * t * y1);
    }
}

static void flat_cubic(Poly *pl, float x0, float y0, float c1x, float c1y, float c2x, float c2y, float x1, float y1, float tol) {
    float len = hypotf(c1x - x0, c1y - y0) + hypotf(c2x - c1x, c2y - c1y) + hypotf(x1 - c2x, y1 - c2y);
    int steps = (int)ceilf(len / tol);
    if (steps < 2) steps = 2;
    if (steps > 256) steps = 256;
    for (int i = 1; i <= steps; i++) {
        float t = (float)i / steps, u = 1 - t;
        float a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
        poly_add(pl, a * x0 + b * c1x + c * c2x + d * x1, a * y0 + b * c1y + c * c2y + d * y1);
    }
}

/* SVG arc (endpoint form) to cubic curves, appended to the current polyline. */
static void flat_arc(Poly *pl, float x0, float y0, float rx, float ry, float phi_deg, int large, int sweep, float x1, float y1, float tol) {
    if (rx == 0 || ry == 0) { poly_add(pl, x1, y1); return; }
    rx = fabsf(rx); ry = fabsf(ry);
    double phi = phi_deg * M_PI / 180, cp = cos(phi), sp = sin(phi);
    double dx = (x0 - x1) / 2.0, dy = (y0 - y1) / 2.0;
    double x1p = cp * dx + sp * dy, y1p = -sp * dx + cp * dy;
    double lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry);
    if (lam > 1) { rx *= (float)sqrt(lam); ry *= (float)sqrt(lam); }
    double num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p;
    double den = rx * rx * y1p * y1p + ry * ry * x1p * x1p;
    double co = den == 0 ? 0 : sqrt(fmax(0, num / den));
    if (large == sweep) co = -co;
    double cxp = co * rx * y1p / ry, cyp = -co * ry * x1p / rx;
    double cx = cp * cxp - sp * cyp + (x0 + x1) / 2.0, cy = sp * cxp + cp * cyp + (y0 + y1) / 2.0;
    double th1 = atan2((y1p - cyp) / ry, (x1p - cxp) / rx);
    double th2 = atan2((-y1p - cyp) / ry, (-x1p - cxp) / rx);
    double dth = th2 - th1;
    if (sweep && dth < 0) dth += 2 * M_PI;
    if (!sweep && dth > 0) dth -= 2 * M_PI;
    double arclen = fabs(dth) * fmax(rx, ry);
    int steps = (int)ceil(arclen / tol);
    if (steps < 4) steps = 4;
    if (steps > 512) steps = 512;
    for (int i = 1; i <= steps; i++) {
        double th = th1 + dth * i / steps;
        double ex = rx * cos(th), ey = ry * sin(th);
        poly_add(pl, (float)(cp * ex - sp * ey + cx), (float)(sp * ex + cp * ey + cy));
    }
}

/* Parse an SVG path "d" into polylines. */
static int svg_number(const char **sp, float *out) {
    const char *s = *sp;
    while (*s == ' ' || *s == ',' || *s == '\t' || *s == '\n' || *s == '\r') s++;
    if (!*s || !(isdigit((unsigned char)*s) || *s == '-' || *s == '+' || *s == '.')) { *sp = s; return 0; }
    char *end;
    *out = strtof(s, &end);
    if (end == s) return 0;
    *sp = end;
    return 1;
}

static int svg_flag(const char **sp, int *out) {
    const char *s = *sp;
    while (*s == ' ' || *s == ',' || *s == '\t' || *s == '\n' || *s == '\r') s++;
    if (*s != '0' && *s != '1') return 0;
    *out = *s - '0';
    *sp = s + 1;
    return 1;
}

static void svg_parse(Shape *sh, const char *d, float tol) {
    const char *s = d;
    float cx = 0, cy = 0, sx = 0, sy = 0, lcx = 0, lcy = 0;
    char cmd = 0, prev = 0;
    Poly *pl = NULL;
    while (*s) {
        while (*s == ' ' || *s == ',' || *s == '\t' || *s == '\n' || *s == '\r') s++;
        if (!*s) break;
        if (isalpha((unsigned char)*s)) { cmd = *s++; }
        else if (!cmd) break;
        int rel = islower((unsigned char)cmd);
        char C = (char)toupper((unsigned char)cmd);
        float a[7];
        if (C == 'Z') {
            if (pl) { pl->closed = 1; }
            cx = sx; cy = sy;
            pl = NULL;
            prev = 'Z';
            cmd = 0;
            continue;
        }
        if (C == 'M') {
            if (!svg_number(&s, &a[0]) || !svg_number(&s, &a[1])) break;
            cx = rel ? cx + a[0] : a[0]; cy = rel ? cy + a[1] : a[1];
            sx = cx; sy = cy;
            pl = shape_new_poly(sh);
            poly_add(pl, cx, cy);
            cmd = rel ? 'l' : 'L';  /* further pairs are lines */
            prev = 'M';
            continue;
        }
        if (!pl) { pl = shape_new_poly(sh); poly_add(pl, cx, cy); sx = cx; sy = cy; }
        if (C == 'L') {
            if (!svg_number(&s, &a[0]) || !svg_number(&s, &a[1])) break;
            cx = rel ? cx + a[0] : a[0]; cy = rel ? cy + a[1] : a[1];
            poly_add(pl, cx, cy);
        } else if (C == 'H') {
            if (!svg_number(&s, &a[0])) break;
            cx = rel ? cx + a[0] : a[0];
            poly_add(pl, cx, cy);
        } else if (C == 'V') {
            if (!svg_number(&s, &a[0])) break;
            cy = rel ? cy + a[0] : a[0];
            poly_add(pl, cx, cy);
        } else if (C == 'C' || C == 'S') {
            float c1x, c1y;
            int k = 0;
            if (C == 'C') { if (!svg_number(&s, &a[0]) || !svg_number(&s, &a[1])) break; k = 2; }
            if (!svg_number(&s, &a[k]) || !svg_number(&s, &a[k + 1]) || !svg_number(&s, &a[k + 2]) || !svg_number(&s, &a[k + 3])) break;
            if (C == 'C') { c1x = rel ? cx + a[0] : a[0]; c1y = rel ? cy + a[1] : a[1]; }
            else if (toupper((unsigned char)prev) == 'C' || toupper((unsigned char)prev) == 'S') { c1x = 2 * cx - lcx; c1y = 2 * cy - lcy; }
            else { c1x = cx; c1y = cy; }
            float c2x = rel ? cx + a[k] : a[k], c2y = rel ? cy + a[k + 1] : a[k + 1];
            float ex = rel ? cx + a[k + 2] : a[k + 2], ey = rel ? cy + a[k + 3] : a[k + 3];
            flat_cubic(pl, cx, cy, c1x, c1y, c2x, c2y, ex, ey, tol);
            lcx = c2x; lcy = c2y; cx = ex; cy = ey;
        } else if (C == 'Q' || C == 'T') {
            float qx, qy;
            if (C == 'Q') {
                if (!svg_number(&s, &a[0]) || !svg_number(&s, &a[1]) || !svg_number(&s, &a[2]) || !svg_number(&s, &a[3])) break;
                qx = rel ? cx + a[0] : a[0]; qy = rel ? cy + a[1] : a[1];
                a[0] = a[2]; a[1] = a[3];
            } else {
                if (!svg_number(&s, &a[0]) || !svg_number(&s, &a[1])) break;
                if (toupper((unsigned char)prev) == 'Q' || toupper((unsigned char)prev) == 'T') { qx = 2 * cx - lcx; qy = 2 * cy - lcy; }
                else { qx = cx; qy = cy; }
            }
            float ex = rel ? cx + a[0] : a[0], ey = rel ? cy + a[1] : a[1];
            flat_quad(pl, cx, cy, qx, qy, ex, ey, tol);
            lcx = qx; lcy = qy; cx = ex; cy = ey;
        } else if (C == 'A') {
            int large, sweep;
            if (!svg_number(&s, &a[0]) || !svg_number(&s, &a[1]) || !svg_number(&s, &a[2]) || !svg_flag(&s, &large) ||
                !svg_flag(&s, &sweep) || !svg_number(&s, &a[3]) || !svg_number(&s, &a[4])) break;
            float ex = rel ? cx + a[3] : a[3], ey = rel ? cy + a[4] : a[4];
            flat_arc(pl, cx, cy, a[0], a[1], a[2], large, sweep, ex, ey, tol);
            cx = ex; cy = ey;
        } else {
            break; /* unknown command: stop where it is */
        }
        prev = cmd;
    }
}

static float poly_length(const Poly *pl) {
    float len = 0;
    for (int i = 1; i < pl->n; i++) len += hypotf(pl->p[i].x - pl->p[i - 1].x, pl->p[i].y - pl->p[i - 1].y);
    if (pl->closed && pl->n > 1) len += hypotf(pl->p[0].x - pl->p[pl->n - 1].x, pl->p[0].y - pl->p[pl->n - 1].y);
    return len;
}

/* The first `budget` units of a polyline (for the `draw` dash), as an open polyline. */
static float poly_trim(const Poly *in, float budget, Poly *out) {
    memset(out, 0, sizeof(*out));
    if (in->n == 0) return budget;
    poly_add(out, in->p[0].x, in->p[0].y);
    int segs = in->closed ? in->n : in->n - 1;
    for (int i = 0; i < segs && budget > 0; i++) {
        Pt a = in->p[i], b = in->p[(i + 1) % in->n];
        float l = hypotf(b.x - a.x, b.y - a.y);
        if (l <= budget) { poly_add(out, b.x, b.y); budget -= l; }
        else { float t = budget / l; poly_add(out, a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t); budget = 0; }
    }
    return budget;
}

static float signed_area(const Pt *p, int n) {
    double a = 0;
    for (int i = 0; i < n; i++) { const Pt *u = &p[i], *v = &p[(i + 1) % n]; a += (double)u->x * v->y - (double)v->x * u->y; }
    return (float)(a / 2);
}

static void add_oriented(Shape *out, const Pt *p, int n) {
    Poly *pl = shape_new_poly(out);
    pl->closed = 1;
    int reverse = signed_area(p, n) < 0;
    for (int i = 0; i < n; i++) { const Pt *q = &p[reverse ? n - 1 - i : i]; poly_add(pl, q->x, q->y); }
}

static void add_circle(Shape *out, float cx, float cy, float r, float tol) {
    int n = (int)ceilf(2 * (float)M_PI * r / tol);
    if (n < 8) n = 8;
    if (n > 128) n = 128;
    Pt pts[128];
    for (int i = 0; i < n; i++) { double a = 2 * M_PI * i / n; pts[i].x = cx + r * (float)cos(a); pts[i].y = cy + r * (float)sin(a); }
    add_oriented(out, pts, n);
}

/* Round-join, round-cap stroke of one polyline, as filled polygons that all wind the same way. */
static void stroke_poly(Shape *out, const Poly *pl, float width, float tol) {
    float hw = width / 2;
    if (hw <= 0 || pl->n == 0) return;
    if (pl->n == 1) { add_circle(out, pl->p[0].x, pl->p[0].y, hw, tol); return; }
    int segs = pl->closed ? pl->n : pl->n - 1;
    for (int i = 0; i < segs; i++) {
        Pt a = pl->p[i], b = pl->p[(i + 1) % pl->n];
        float dx = b.x - a.x, dy = b.y - a.y, l = hypotf(dx, dy);
        if (l < 1e-6f) continue;
        float nx = -dy / l * hw, ny = dx / l * hw;
        Pt q[4] = {{a.x + nx, a.y + ny}, {b.x + nx, b.y + ny}, {b.x - nx, b.y - ny}, {a.x - nx, a.y - ny}};
        add_oriented(out, q, 4);
    }
    /* joins: a circle where the direction turns enough to leave a visible notch; caps at open ends */
    for (int i = 0; i < pl->n; i++) {
        int open_end = !pl->closed && (i == 0 || i == pl->n - 1);
        if (!open_end) {
            Pt p0 = pl->p[(i - 1 + pl->n) % pl->n], p1 = pl->p[i], p2 = pl->p[(i + 1) % pl->n];
            float ax = p1.x - p0.x, ay = p1.y - p0.y, bx = p2.x - p1.x, by = p2.y - p1.y;
            float la = hypotf(ax, ay), lb = hypotf(bx, by);
            if (la < 1e-6f || lb < 1e-6f) continue;
            float cosang = (ax * bx + ay * by) / (la * lb);
            if (cosang > 0.985f && hw < 12) continue; /* under ~10 degrees on a thin line: no notch to fill */
        }
        add_circle(out, pl->p[i].x, pl->p[i].y, hw, tol);
    }
}

/* ── rasterizer: signed-area accumulation (as in font-rs), anti-aliased, non-zero ────── */

typedef struct { float x0, y0, x1, y1; } Seg;
typedef struct { Seg *s; int n, cap; float minx, miny, maxx, maxy; } Segs;

static void segs_add(Segs *g, Pt a, Pt b) {
    if (a.y == b.y) return; /* horizontal edges add no area */
    if (g->n == g->cap) { g->cap = g->cap ? g->cap * 2 : 64; g->s = xrealloc(g->s, sizeof(Seg) * (size_t)g->cap); }
    Seg *s = &g->s[g->n++];
    s->x0 = a.x; s->y0 = a.y; s->x1 = b.x; s->y1 = b.y;
    if (g->n == 1) { g->minx = fminf(a.x, b.x); g->maxx = fmaxf(a.x, b.x); g->miny = fminf(a.y, b.y); g->maxy = fmaxf(a.y, b.y); }
    else {
        g->minx = fminf(g->minx, fminf(a.x, b.x)); g->maxx = fmaxf(g->maxx, fmaxf(a.x, b.x));
        g->miny = fminf(g->miny, fminf(a.y, b.y)); g->maxy = fmaxf(g->maxy, fmaxf(a.y, b.y));
    }
}

static void shape_to_segs(const Shape *sh, const Mat *m, Segs *g) {
    for (int i = 0; i < sh->n; i++) {
        const Poly *pl = &sh->v[i];
        if (pl->n < 2) continue;
        for (int j = 0; j < pl->n; j++) {
            Pt a = mat_apply(m, pl->p[j].x, pl->p[j].y), b = mat_apply(m, pl->p[(j + 1) % pl->n].x, pl->p[(j + 1) % pl->n].y);
            segs_add(g, a, b); /* every fill is closed */
        }
    }
}

/* One line into the accumulation buffer (w+2 wide, h rows), x already inside [0, w]. */
static void acc_line(float *acc, int w, int h, float x0, float y0, float x1, float y1) {
    if (y0 == y1) return;
    float dir = 1;
    if (y0 > y1) { float t; t = x0; x0 = x1; x1 = t; t = y0; y0 = y1; y1 = t; dir = -1; }
    float dxdy = (x1 - x0) / (y1 - y0);
    float x = x0;
    int ys = (int)floorf(y0);
    if (y0 < 0) { x -= y0 * dxdy; ys = 0; }
    int ye = (int)ceilf(y1);
    if (ye > h) ye = h;
    int stride = w + 2;
    for (int y = ys; y < ye; y++) {
        float dy = fminf((float)(y + 1), y1) - fmaxf((float)y, y0);
        float xnext = x + dxdy * dy;
        float d = dy * dir;
        float xa = x < xnext ? x : xnext, xb = x < xnext ? xnext : x;
        float xaf = floorf(xa);
        int xai = (int)xaf;
        float xbc = ceilf(xb);
        int xbi = (int)xbc;
        float *row = acc + (size_t)y * stride;
        if (xai < 0) xai = 0;
        if (xbi > w + 1) xbi = w + 1;
        if (xbi <= xai + 1) {
            float xmf = 0.5f * (x + xnext) - xaf;
            row[xai] += d - d * xmf;
            row[xai + 1] += d * xmf;
        } else {
            float s = 1.0f / (xb - xa);
            float x0f = xa - xaf;
            float a0 = 0.5f * s * (1 - x0f) * (1 - x0f);
            float x1f = xb - xbc + 1;
            float am = 0.5f * s * x1f * x1f;
            row[xai] += d * a0;
            if (xbi == xai + 2) {
                row[xai + 1] += d * (1 - a0 - am);
            } else {
                float a1 = s * (1.5f - x0f);
                row[xai + 1] += d * (a1 - a0);
                for (int xi = xai + 2; xi < xbi - 1; xi++) row[xi] += d * s;
                float a2 = a1 + (xbi - xai - 3) * s;
                row[xbi - 1] += d * (1 - a2 - am);
            }
            if (xbi <= w + 1) row[xbi] += d * am;
        }
        x = xnext;
    }
}

/* Clip a segment to x in [0, w]: parts outside become vertical runs on the edge (area stays right). */
static void acc_seg(float *acc, int w, int h, float x0, float y0, float x1, float y1) {
    float ts[4] = {0, 0, 0, 1};
    int nt = 1;
    if (x0 != x1) {
        float t0 = (0 - x0) / (x1 - x0), tw = ((float)w - x0) / (x1 - x0);
        if (t0 > 0 && t0 < 1) ts[nt++] = t0;
        if (tw > 0 && tw < 1) ts[nt++] = tw;
    }
    ts[nt++] = 1;
    /* sort the (at most 4) cut points */
    for (int i = 0; i < nt; i++)
        for (int j = i + 1; j < nt; j++)
            if (ts[j] < ts[i]) { float t = ts[i]; ts[i] = ts[j]; ts[j] = t; }
    for (int i = 0; i + 1 < nt; i++) {
        float ta = ts[i], tb = ts[i + 1];
        if (tb - ta <= 0) continue;
        float ax = x0 + (x1 - x0) * ta, ay = y0 + (y1 - y0) * ta;
        float bx = x0 + (x1 - x0) * tb, by = y0 + (y1 - y0) * tb;
        float mx = (ax + bx) / 2;
        if (mx < 0) { ax = bx = 0; }
        else if (mx > w) { ax = bx = (float)w; }
        ax = clampf(ax, 0, (float)w); bx = clampf(bx, 0, (float)w);
        acc_line(acc, w, h, ax, ay, bx, by);
    }
}

typedef struct { float *px; int w, h; } Canvas; /* premultiplied RGBA floats */

/* Fill segments with a premultiplied colour, only rows [ry0, ry1). acc is scratch of (W+2)*(rows) floats. */
static void fill_segs(Canvas *cv, const Segs *g, const float *col, int ry0, int ry1, float *acc, size_t acc_cap) {
    if (g->n == 0 || col[3] <= 0) return;
    int x0 = (int)floorf(g->minx), x1 = (int)ceilf(g->maxx) + 1;
    int y0 = (int)floorf(g->miny), y1 = (int)ceilf(g->maxy) + 1;
    if (x0 < 0) x0 = 0;
    if (x1 > cv->w) x1 = cv->w;
    if (y0 < ry0) y0 = ry0;
    if (y1 > ry1) y1 = ry1;
    if (x0 >= x1 || y0 >= y1) return;
    int w = x1 - x0, h = y1 - y0;
    size_t need = (size_t)(w + 2) * h;
    if (need > acc_cap) die(3, "scratch buffer too small (%zu > %zu)", need, acc_cap);
    memset(acc, 0, need * sizeof(float));
    for (int i = 0; i < g->n; i++) {
        const Seg *s = &g->s[i];
        if (fmaxf(s->y0, s->y1) <= y0 || fminf(s->y0, s->y1) >= y1) continue;
        acc_seg(acc, w, h, s->x0 - x0, s->y0 - y0, s->x1 - x0, s->y1 - y0);
    }
    for (int y = 0; y < h; y++) {
        float *row = acc + (size_t)y * (w + 2);
        float *dst = cv->px + ((size_t)(y + y0) * cv->w + x0) * 4;
        float sum = 0;
        for (int x = 0; x < w; x++, dst += 4) {
            sum += row[x];
            float cov = fabsf(sum);
            if (cov > 1) cov = 1;
            if (cov <= 1e-4f) continue;
            float a = col[3] * cov, inv = 1 - a;
            dst[0] = col[0] * cov + dst[0] * inv;
            dst[1] = col[1] * cov + dst[1] * inv;
            dst[2] = col[2] * cov + dst[2] * inv;
            dst[3] = a + dst[3] * inv;
        }
    }
}

/* ── pictures (image and video layers) ─────────────────────────────────────── */

static void sample_level(const Level *lv, float u, float v, float *out) {
    /* bilinear, u/v in level pixels, edge clamped; output premultiplied */
    u -= 0.5f; v -= 0.5f;
    int xi = (int)floorf(u), yi = (int)floorf(v);
    float fx = u - xi, fy = v - yi;
    float acc[4] = {0, 0, 0, 0};
    for (int j = 0; j < 2; j++)
        for (int i = 0; i < 2; i++) {
            int x = xi + i, y = yi + j;
            if (x < 0) x = 0;
            if (y < 0) y = 0;
            if (x >= lv->w) x = lv->w - 1;
            if (y >= lv->h) y = lv->h - 1;
            const unsigned char *p = lv->px + ((size_t)y * lv->w + x) * 4;
            float wgt = (i ? fx : 1 - fx) * (j ? fy : 1 - fy);
            float a = p[3] / 255.0f;
            acc[0] += p[0] / 255.0f * a * wgt; acc[1] += p[1] / 255.0f * a * wgt;
            acc[2] += p[2] / 255.0f * a * wgt; acc[3] += a * wgt;
        }
    memcpy(out, acc, sizeof(acc));
}

static float sd_round_rect(float px, float py, float hw, float hh, float r) {
    r = fminf(r, fminf(hw, hh));
    float qx = fabsf(px) - hw + r, qy = fabsf(py) - hh + r;
    float ox = fmaxf(qx, 0), oy = fmaxf(qy, 0);
    return hypotf(ox, oy) + fminf(fmaxf(qx, qy), 0) - r;
}

/* Draw a picture (levels) into the layer box (bw x bh, centred), contain or cover, clipped with radius. */
static void draw_picture(Canvas *cv, const Mat *m, const Level *levels, int nlevels, float bw, float bh, float radius,
                         int cover, float opacity, int ry0, int ry1) {
    if (nlevels <= 0 || !levels[0].px || opacity <= 0) return;
    const Level *base = &levels[0];
    float k = cover ? fmaxf(bw / base->w, bh / base->h) : fminf(bw / base->w, bh / base->h);
    float dw = base->w * k, dh = base->h * k;
    float s = (float)mat_scale(m);
    /* the level whose pixels are closest to (not smaller than) the output size */
    float px_per_texel = k * s;
    int lvl = 0;
    while (lvl + 1 < nlevels && px_per_texel * (float)(1 << (lvl + 1)) <= 1.0f) lvl++;
    const Level *L = &levels[lvl];
    Mat inv;
    if (!mat_invert(m, &inv)) return;
    /* device bbox of the box corners */
    Pt c[4] = {mat_apply(m, -bw / 2, -bh / 2), mat_apply(m, bw / 2, -bh / 2), mat_apply(m, bw / 2, bh / 2), mat_apply(m, -bw / 2, bh / 2)};
    float minx = c[0].x, maxx = c[0].x, miny = c[0].y, maxy = c[0].y;
    for (int i = 1; i < 4; i++) { minx = fminf(minx, c[i].x); maxx = fmaxf(maxx, c[i].x); miny = fminf(miny, c[i].y); maxy = fmaxf(maxy, c[i].y); }
    int x0 = (int)floorf(minx), x1 = (int)ceilf(maxx), y0 = (int)floorf(miny), y1 = (int)ceilf(maxy);
    if (x0 < 0) x0 = 0;
    if (x1 > cv->w) x1 = cv->w;
    if (y0 < ry0) y0 = ry0;
    if (y1 > ry1) y1 = ry1;
    float hw = fminf(bw, dw) / 2, hh = fminf(bh, dh) / 2; /* visible area: box ∩ picture */
    float rr = radius > 0 ? radius : 0;
    for (int y = y0; y < y1; y++) {
        float *dst = cv->px + ((size_t)y * cv->w + x0) * 4;
        for (int x = x0; x < x1; x++, dst += 4) {
            Pt l = mat_apply(&inv, x + 0.5f, y + 0.5f);
            float d = sd_round_rect(l.x, l.y, hw, hh, cover ? rr : fminf(rr, fminf(hw, hh)));
            float cov = clampf(0.5f - d * s, 0, 1);
            if (cov <= 0) continue;
            float u = (l.x + dw / 2) / dw * L->w, v = (l.y + dh / 2) / dh * L->h;
            float p[4];
            sample_level(L, u, v, p);
            float a = cov * opacity;
            float inv_a = 1 - p[3] * a;
            dst[0] = p[0] * a + dst[0] * inv_a;
            dst[1] = p[1] * a + dst[1] * inv_a;
            dst[2] = p[2] * a + dst[2] * inv_a;
            dst[3] = p[3] * a + dst[3] * inv_a;
        }
    }
}

/* ── video clips through ffmpeg pipes ──────────────────────────────────────── */

/* The frame for timeline time t, read in order from the clip's ffmpeg pipe. 0 when there is none. */
static int video_frame(Video *v, double t, double fps) {
    long want = (long)floor((t - v->t0) * fps + 1e-6);
    if (want < 0) want = 0;
    if (want == v->have) return 1;
    if (v->ended && want > v->have) return v->have >= 0;
    if (!v->pipe) {
        if (!v->cmd || !*v->cmd) return 0;
        v->pipe = popen(v->cmd, PIPE_READ);
        if (!v->pipe) { fprintf(stderr, "sqengine: cannot start decoder for %s\n", v->id); v->ended = 1; return 0; }
        v->buf = xmalloc((size_t)v->w * v->h * 4);
        v->next = 0;
    }
    if (want < v->next - 1 && v->have >= 0) return 1; /* went backwards (should not happen): keep the last frame */
    size_t bytes = (size_t)v->w * v->h * 4;
    while (v->next <= want) {
        size_t got = fread(v->buf, 1, bytes, v->pipe);
        if (got != bytes) { v->ended = 1; pclose(v->pipe); v->pipe = NULL; break; }
        v->have = v->next++;
    }
    return v->have >= 0;
}

/* ── text ──────────────────────────────────────────────────────────────────── */

static int utf8_next(const char **sp) {
    const unsigned char *s = (const unsigned char *)*sp;
    int c = *s;
    if (!c) return 0;
    int n = c < 0x80 ? 1 : (c >> 5) == 6 ? 2 : (c >> 4) == 14 ? 3 : (c >> 3) == 30 ? 4 : 1;
    int cp = n == 1 ? c : n == 2 ? c & 31 : n == 3 ? c & 15 : c & 7;
    for (int i = 1; i < n; i++) {
        if ((s[i] & 0xC0) != 0x80) { n = i; break; }
        cp = (cp << 6) | (s[i] & 63);
    }
    *sp += n;
    return cp;
}

static float text_width(const Font *f, float scale, const char *s, size_t len) {
    if (!f || !f->ok) return 0;
    const char *p = s, *end = s + len;
    float w = 0;
    int prev = 0;
    while (p < end && *p) {
        int cp = utf8_next(&p);
        int adv, lsb;
        stbtt_GetCodepointHMetrics(&f->info, cp, &adv, &lsb);
        if (prev) w += stbtt_GetCodepointKernAdvance(&f->info, prev, cp) * scale;
        w += adv * scale;
        prev = cp;
    }
    return w;
}

typedef struct { const char *s; size_t len; float w; } Line;

/* Lines after wrapping at `width` (0 = only at newlines). Returns the count; *out is malloc'd. */
static int text_lines(const Font *f, float scale, const char *text, float width, Line **out) {
    int cap = 8, n = 0;
    Line *lines = xmalloc(sizeof(Line) * (size_t)cap);
    const char *para = text;
    for (;;) {
        const char *nl = strchr(para, '\n');
        size_t plen = nl ? (size_t)(nl - para) : strlen(para);
        if (width <= 0) {
            if (n == cap) { cap *= 2; lines = xrealloc(lines, sizeof(Line) * (size_t)cap); }
            lines[n].s = para; lines[n].len = plen; lines[n].w = text_width(f, scale, para, plen); n++;
        } else {
            /* greedy word wrap, like render.js */
            const char *p = para, *pend = para + plen;
            const char *ls = NULL, *le = NULL;
            while (p < pend) {
                while (p < pend && (*p == ' ' || *p == '\t')) p++;
                if (p >= pend) break;
                const char *ws = p;
                while (p < pend && *p != ' ' && *p != '\t') p++;
                const char *we = p;
                if (!ls) { ls = ws; le = we; continue; }
                if (text_width(f, scale, ls, (size_t)(we - ls)) > width) {
                    if (n == cap) { cap *= 2; lines = xrealloc(lines, sizeof(Line) * (size_t)cap); }
                    lines[n].s = ls; lines[n].len = (size_t)(le - ls); lines[n].w = text_width(f, scale, ls, lines[n].len); n++;
                    ls = ws; le = we;
                } else {
                    le = we;
                }
            }
            if (n == cap) { cap *= 2; lines = xrealloc(lines, sizeof(Line) * (size_t)cap); }
            if (ls) { lines[n].s = ls; lines[n].len = (size_t)(le - ls); }
            else { lines[n].s = para; lines[n].len = 0; }
            lines[n].w = text_width(f, scale, lines[n].s, lines[n].len);
            n++;
        }
        if (!nl) break;
        para = nl + 1;
    }
    *out = lines;
    return n;
}

/* Glyph outlines of the text, in layer space, laid out like render.js (textBaseline middle). */
static void text_shape(const Layer *L, float px, float draw, Shape *out, float tol, float *box_w, float *box_h) {
    const Font *f = L->font;
    float lh = px * (L->line_height > 0 ? L->line_height : 1.15f);
    if (!f || !f->ok || !L->text) { *box_w = L->width > 0 ? L->width : px * 4; *box_h = lh; return; }
    float scale = stbtt_ScaleForMappingEmToPixels(&f->info, px);
    Line *lines;
    int n = text_lines(f, scale, L->text, L->width, &lines);
    float block = L->width;
    if (block <= 0) { block = 0; for (int i = 0; i < n; i++) block = fmaxf(block, lines[i].w); }
    *box_w = L->width > 0 ? L->width : fmaxf(block, 10);
    *box_h = n * lh;
    int asc, desc, gap;
    stbtt_GetFontVMetrics(&f->info, &asc, &desc, &gap);
    float mid = (asc + desc) * scale / 2; /* baseline = y + mid puts the em box's middle on y */
    /* typing: how many characters `draw` lets through */
    long total = 0;
    for (int i = 0; i < n; i++) { const char *p = lines[i].s, *e = p + lines[i].len; while (p < e && *p) { utf8_next(&p); total++; } }
    long budget = draw < 1 ? lroundf((float)total * draw) : -1;
    for (int i = 0; i < n; i++) {
        float y = -((n - 1) * lh) / 2 + i * lh + mid;
        float x = L->align == 0 ? -block / 2 : L->align == 2 ? block / 2 - lines[i].w : -lines[i].w / 2;
        const char *p = lines[i].s, *e = p + lines[i].len;
        int prev = 0;
        while (p < e && *p) {
            if (budget == 0) break;
            int cp = utf8_next(&p);
            if (budget > 0) budget--;
            if (prev) x += stbtt_GetCodepointKernAdvance(&f->info, prev, cp) * scale;
            stbtt_vertex *v;
            int nv = stbtt_GetCodepointShape(&f->info, cp, &v);
            Poly *pl = NULL;
            float lx = 0, ly = 0;
            for (int k = 0; k < nv; k++) {
                float vx = x + v[k].x * scale, vy = y - v[k].y * scale;
                if (v[k].type == STBTT_vmove) { pl = shape_new_poly(out); pl->closed = 1; poly_add(pl, vx, vy); }
                else if (pl && v[k].type == STBTT_vline) poly_add(pl, vx, vy);
                else if (pl && v[k].type == STBTT_vcurve)
                    flat_quad(pl, lx, ly, x + v[k].cx * scale, y - v[k].cy * scale, vx, vy, tol);
                else if (pl && v[k].type == STBTT_vcubic)
                    flat_cubic(pl, lx, ly, x + v[k].cx * scale, y - v[k].cy * scale, x + v[k].cx1 * scale, y - v[k].cy1 * scale, vx, vy, tol);
                lx = vx; ly = vy;
            }
            stbtt_FreeShape(&f->info, v);
            int adv, lsb;
            stbtt_GetCodepointHMetrics(&f->info, cp, &adv, &lsb);
            x += adv * scale;
            prev = cp;
        }
        if (budget == 0) break;
    }
    free(lines);
}

/* ── one layer, prepared once per frame and drawn by every band ─────────────── */

typedef struct {
    int visible;
    int kind; /* 0 vector, 1 picture */
    Segs fill, stroke;
    float fillc[4], strokec[4]; /* premultiplied, opacity applied */
    /* pictures */
    Mat m;
    const Level *levels; int nlevels;
    Level frame; /* video frame wrapper */
    float bw, bh, radius, opacity;
    int cover;
    float blur; /* device pixels */
    /* effects: colour, chroma key, LUT, mask and wipes, applied to the layer alone before it is composited */
    int isolate;
    const Layer *L;
    float color[5]; /* brightness, contrast, saturation, hue, temperature */
    int nwipe, wipe_type[2], wipe_out[2]; float wipe_a[2];
    float box_w, box_h;         /* the layer's own box, for masks and wipes */
    Mat inv; float dscale;      /* device -> layer space, and device pixels per layer unit */
    int bx0, by0, bx1, by1;     /* device rows/columns the layer can touch */
} Prepared;

/* ── effects: the same arithmetic as render.js (effectPixel), so preview and export agree ── */

static void effect_pixel(const Prepared *pr, float *rgb, float *alpha) {
    const Layer *L = pr->L;
    float r = rgb[0], g = rgb[1], b = rgb[2];
    if (L->has_chroma) {
        float y = 0.299f * r + 0.587f * g + 0.114f * b;
        float cb = (b - y) * 0.564f, cr = (r - y) * 0.713f;
        float d = hypotf(cb - L->kcb, cr - L->kcr);
        float soft = fmaxf(1e-4f, L->ksoft * 0.5f);
        *alpha *= clampf((d - L->ksim * 0.5f) / soft, 0, 1);
        float klen = hypotf(L->kcb, L->kcr);
        if (L->kspill > 0 && klen > 1e-6f) {
            float proj = (cb * L->kcb + cr * L->kcr) / klen;
            if (proj > 0) {
                cb -= L->kspill * proj * L->kcb / klen;
                cr -= L->kspill * proj * L->kcr / klen;
                r = y + cr / 0.713f;
                b = y + cb / 0.564f;
                g = (y - 0.299f * r - 0.114f * b) / 0.587f;
            }
        }
    }
    if (L->lut && L->lut_mix > 0) {
        float in[3] = {clampf(r, 0, 1), clampf(g, 0, 1), clampf(b, 0, 1)}, out[3];
        lut_sample(L->lut, in, out);
        r += (out[0] - r) * L->lut_mix; g += (out[1] - g) * L->lut_mix; b += (out[2] - b) * L->lut_mix;
    }
    const float *c = pr->color;
    if (c[4] != 0) { r += c[4] * 0.1f; b -= c[4] * 0.1f; }
    if (c[0] != 0) { float k = 1 + c[0]; r *= k; g *= k; b *= k; }
    if (c[1] != 0) { float k = 1 + c[1]; r = (r - 0.5f) * k + 0.5f; g = (g - 0.5f) * k + 0.5f; b = (b - 0.5f) * k + 0.5f; }
    if (c[2] != 0) {
        float l = 0.2126f * r + 0.7152f * g + 0.0722f * b, k = 1 + c[2];
        r = l + (r - l) * k; g = l + (g - l) * k; b = l + (b - l) * k;
    }
    if (c[3] != 0) { /* the CSS hue-rotate matrix */
        float a = c[3] * (float)M_PI / 180, co = cosf(a), si = sinf(a);
        float nr = r * (0.213f + co * 0.787f - si * 0.213f) + g * (0.715f - co * 0.715f - si * 0.715f) + b * (0.072f - co * 0.072f + si * 0.928f);
        float ng = r * (0.213f - co * 0.213f + si * 0.143f) + g * (0.715f + co * 0.285f + si * 0.140f) + b * (0.072f - co * 0.072f - si * 0.283f);
        float nb = r * (0.213f - co * 0.213f - si * 0.787f) + g * (0.715f - co * 0.715f + si * 0.715f) + b * (0.072f + co * 0.928f + si * 0.072f);
        r = nr; g = ng; b = nb;
    }
    rgb[0] = clampf(r, 0, 1); rgb[1] = clampf(g, 0, 1); rgb[2] = clampf(b, 0, 1);
}

/* How much of the layer stays at layer-space point (x, y): its mask times its wipes, 0..1. */
static float coverage(const Prepared *pr, float x, float y) {
    const Layer *L = pr->L;
    float s = pr->dscale > 1e-6f ? pr->dscale : 1, cov = 1;
    if (L->has_mask) {
        float hw = fabsf(L->mask_w) / 2, hh = fabsf(L->mask_h) / 2, d;
        float px = x - L->mask_x, py = y - L->mask_y;
        if (L->mask_ellipse) d = (hypotf(px / fmaxf(hw, 1e-3f), py / fmaxf(hh, 1e-3f)) - 1) * fminf(hw, hh);
        else d = sd_round_rect(px, py, hw, hh, fmaxf(0, L->mask_r));
        float m = clampf(0.5f - d / (fmaxf(0, L->mask_feather) + 1 / s), 0, 1);
        cov *= L->mask_invert ? 1 - m : m;
    }
    for (int i = 0; i < pr->nwipe; i++) {
        float a = pr->wipe_a[i], w = pr->box_w, h = pr->box_h, d;
        int out = pr->wipe_out[i];
        switch (pr->wipe_type[i]) {
        case T_WIPE_R: d = out ? (-w / 2 + a * w) - x : x - (-w / 2 + (1 - a) * w); break;
        case T_WIPE_L: d = out ? x - (w / 2 - a * w) : (w / 2 - (1 - a) * w) - x; break;
        case T_WIPE_D: d = out ? (-h / 2 + a * h) - y : y - (-h / 2 + (1 - a) * h); break;
        case T_WIPE_U: d = out ? y - (h / 2 - a * h) : (h / 2 - (1 - a) * h) - y; break;
        default: d = hypotf(x, y) - (1 - a) * hypotf(w, h) / 2; break; /* iris */
        }
        cov *= clampf(0.5f - d * s, 0, 1);
    }
    return cov;
}

/* The amount (0 shown .. 1 gone) a transition takes away at time t. */
static float trans_amount(const Trans *tr, double t, double start, double end, int out) {
    if (tr->type == T_NONE || tr->dur <= 0) return 0;
    double p = out ? (end - t) / tr->dur : (t - start) / tr->dur;
    if (p >= 1) return 0;
    if (p < 0) p = 0;
    return (float)(1 - ease(&tr->ease, p));
}

static void apply_transitions(const Scene *sc, const Layer *L, double t, float *st, Prepared *pr) {
    for (int side = 0; side < 2; side++) {
        const Trans *tr = side ? &L->tout : &L->tin;
        float a = trans_amount(tr, t, L->start, L->end, side);
        if (a == 0) continue;
        float sign = side ? -1.0f : 1.0f; /* in: from the far side; out: on to the far side */
        switch (tr->type) {
        case T_FADE: st[P_OPACITY] *= 1 - a; break;
        case T_SLIDE_L: st[P_X] += sign * a * (float)sc->w; break;
        case T_SLIDE_R: st[P_X] -= sign * a * (float)sc->w; break;
        case T_SLIDE_U: st[P_Y] += sign * a * (float)sc->h; break;
        case T_SLIDE_D: st[P_Y] -= sign * a * (float)sc->h; break;
        case T_ZOOM: st[P_SCALE] *= side ? 1 + 0.3f * a : 1 - 0.3f * a; st[P_OPACITY] *= 1 - a; break;
        case T_BLUR: st[P_BLUR] += a * 0.03f * (float)(sc->w < sc->h ? sc->w : sc->h); st[P_OPACITY] *= 1 - a; break;
        case T_WIPE_L: case T_WIPE_R: case T_WIPE_U: case T_WIPE_D: case T_IRIS:
            if (a > 0 && pr->nwipe < 2) {
                pr->wipe_type[pr->nwipe] = tr->type; pr->wipe_out[pr->nwipe] = side; pr->wipe_a[pr->nwipe] = fminf(a, 1);
                pr->nwipe++;
            }
            break;
        default: break;
        }
    }
}

static void premul(const float *c, float opacity, float *out) {
    float a = clampf(c[3], 0, 1) * clampf(opacity, 0, 1);
    out[0] = c[0] * a; out[1] = c[1] * a; out[2] = c[2] * a; out[3] = a;
}

static void prepare_layer(const Scene *sc, Layer *L, double t, const Mat *global, Prepared *pr) {
    memset(pr, 0, sizeof(*pr));
    if (t < L->start || t >= L->end || L->type == L_OTHER) return;
    float st[P_COUNT];
    for (int p = 0; p < P_COUNT; p++) st[p] = track_num(L->num_keys[p], t, L->stat[p]);
    apply_transitions(sc, L, t, st, pr);
    float fill[4], stroke[4];
    int has_fill, has_stroke;
    track_color(L->fill_keys, t, L->fill, L->has_fill, fill, &has_fill);
    track_color(L->stroke_keys, t, L->stroke, L->has_stroke, stroke, &has_stroke);
    float opacity = clampf(st[P_OPACITY], 0, 1);
    if (opacity <= 0) return;
    /* transform: translate(x, y) rotate(r) scale(s*sx, s*sy), inside the global scale */
    double rad = st[P_ROT] * M_PI / 180;
    Mat tr = {1, 0, 0, 1, st[P_X], st[P_Y]};
    Mat rot = {cos(rad), sin(rad), -sin(rad), cos(rad), 0, 0};
    Mat scl = {st[P_SCALE] * st[P_SCALEX], 0, 0, st[P_SCALE] * st[P_SCALEY], 0, 0};
    Mat m = mat_mul(*global, mat_mul(tr, mat_mul(rot, scl)));
    double s = mat_scale(&m);
    if (s < 1e-6) return;
    float tol = (float)(0.35 / s); /* flatten to ~1/3 device pixel */
    float bw = st[P_W] > 0 ? st[P_W] : 100, bh = st[P_H] > 0 ? st[P_H] : 100;
    pr->visible = 1;
    pr->blur = st[P_BLUR] > 0 ? (float)(st[P_BLUR] * mat_scale(global)) : 0;
    pr->opacity = opacity;
    pr->L = L;
    pr->m = m;
    pr->dscale = (float)s;
    for (int c = 0; c < 5; c++) pr->color[c] = st[P_BRIGHT + c];
    pr->isolate = L->has_chroma || (L->lut && L->lut_mix > 0) || L->has_mask || pr->nwipe > 0 ||
                  st[P_BRIGHT] != 0 || st[P_CONTRAST] != 0 || st[P_SAT] != 0 || st[P_HUE] != 0 || st[P_TEMP] != 0;
    pr->box_w = bw; pr->box_h = bh;
    if (pr->isolate && !mat_invert(&m, &pr->inv)) pr->isolate = 0;
    if (L->type == L_IMAGE || L->type == L_VIDEO) {
        pr->kind = 1;
        pr->m = m; pr->bw = bw; pr->bh = bh; pr->radius = fmaxf(0, st[P_RADIUS]); pr->cover = L->cover;
        if (L->type == L_IMAGE && L->image && L->image->levels) { pr->levels = L->image->lv; pr->nlevels = L->image->levels; }
        else if (L->type == L_VIDEO && L->video && video_frame(L->video, t, sc->fps)) {
            pr->frame.w = L->video->w; pr->frame.h = L->video->h; pr->frame.px = L->video->buf;
            pr->levels = &pr->frame; pr->nlevels = 1;
        } else if (L->type == L_VIDEO) {
            /* no frame (yet): the editor shows a dark box; do the same */
            Shape box = {0};
            Poly *pl = shape_new_poly(&box);
            pl->closed = 1;
            poly_add(pl, -bw / 2, -bh / 2); poly_add(pl, bw / 2, -bh / 2); poly_add(pl, bw / 2, bh / 2); poly_add(pl, -bw / 2, bh / 2);
            shape_to_segs(&box, &m, &pr->fill);
            float dark[4] = {0, 0, 0, 0.35f};
            premul(dark, opacity, pr->fillc);
            pr->kind = 0;
            shape_free(&box);
        }
        if (pr->isolate) {
            Pt c[4] = {mat_apply(&m, -bw / 2, -bh / 2), mat_apply(&m, bw / 2, -bh / 2), mat_apply(&m, bw / 2, bh / 2), mat_apply(&m, -bw / 2, bh / 2)};
            float x0 = c[0].x, x1 = c[0].x, y0 = c[0].y, y1 = c[0].y;
            for (int i = 1; i < 4; i++) { x0 = fminf(x0, c[i].x); x1 = fmaxf(x1, c[i].x); y0 = fminf(y0, c[i].y); y1 = fmaxf(y1, c[i].y); }
            pr->bx0 = (int)floorf(x0) - 2; pr->bx1 = (int)ceilf(x1) + 2; pr->by0 = (int)floorf(y0) - 2; pr->by1 = (int)ceilf(y1) + 2;
        }
        return;
    }
    Shape sh = {0};
    float len = 0;
    if (L->type == L_RECT) {
        float r = fmaxf(0, fminf(st[P_RADIUS], fminf(bw / 2, bh / 2)));
        Poly *pl = shape_new_poly(&sh);
        pl->closed = 1;
        if (r <= 0) {
            poly_add(pl, -bw / 2, -bh / 2); poly_add(pl, bw / 2, -bh / 2); poly_add(pl, bw / 2, bh / 2); poly_add(pl, -bw / 2, bh / 2);
        } else {
            /* rounded corners, clockwise from the top-left */
            float cx[4] = {-bw / 2 + r, bw / 2 - r, bw / 2 - r, -bw / 2 + r}, cy[4] = {-bh / 2 + r, -bh / 2 + r, bh / 2 - r, bh / 2 - r};
            int steps = (int)fmaxf(4, fminf(64, ceilf((float)(M_PI / 2 * r / tol))));
            for (int c = 0; c < 4; c++)
                for (int i = 0; i <= steps; i++) {
                    double a = M_PI + c * M_PI / 2 + (M_PI / 2) * i / steps;
                    poly_add(pl, cx[c] + r * (float)cos(a), cy[c] + r * (float)sin(a));
                }
        }
        len = 2 * (bw + bh);
    } else if (L->type == L_ELLIPSE) {
        float a = fabsf(bw / 2), b = fabsf(bh / 2);
        int steps = (int)fmaxf(16, fminf(512, ceilf((float)(2 * M_PI * fmaxf(a, b) / tol))));
        Poly *pl = shape_new_poly(&sh);
        pl->closed = 1;
        /* canvas starts an ellipse at angle 0 (the right-hand point), which matters for the `draw` dash */
        for (int i = 0; i < steps; i++) { double th = 2 * M_PI * i / steps; poly_add(pl, a * (float)cos(th), b * (float)sin(th)); }
        len = (float)(M_PI * (3 * (a + b) - sqrt((3 * a + b) * (a + 3 * b))));
    } else if (L->type == L_POLYGON) {
        float r = st[P_RADIUS] > 0 ? st[P_RADIUS] : 100;
        int nside = L->sides < 3 ? 3 : L->sides > 24 ? 24 : L->sides;
        int count = L->star ? nside * 2 : nside;
        Poly *pl = shape_new_poly(&sh);
        pl->closed = 1;
        for (int i = 0; i < count; i++) {
            float rr = L->star && (i % 2) ? r * L->inner : r;
            double a = -M_PI / 2 + (i * M_PI * 2) / count;
            poly_add(pl, (float)cos(a) * rr, (float)sin(a) * rr);
        }
        len = poly_length(pl);
        bw = bh = r * 2;
    } else if (L->type == L_PATH) {
        if (L->d) svg_parse(&sh, L->d, tol);
        for (int i = 0; i < sh.n; i++) { for (int j = 0; j < sh.v[i].n; j++) { sh.v[i].p[j].x -= bw / 2; sh.v[i].p[j].y -= bh / 2; } len += poly_length(&sh.v[i]); }
    } else if (L->type == L_TEXT) {
        float px = st[P_SIZE] > 0 ? st[P_SIZE] : 48;
        float tw, th;
        text_shape(L, px, st[P_DRAW], &sh, tol, &tw, &th);
        pr->box_w = tw; pr->box_h = th;
    }
    if (L->type == L_POLYGON) { pr->box_w = bw; pr->box_h = bh; }
    /* fill */
    if (has_fill && sh.n) {
        shape_to_segs(&sh, &m, &pr->fill);
        premul(fill, opacity, pr->fillc);
    }
    /* stroke: text strokes its outlines whatever `draw` is (draw types it in instead) */
    int stroke_on = has_stroke && st[P_STROKEW] > 0 && (L->type == L_TEXT || st[P_DRAW] > 0.001f);
    if (stroke_on && sh.n) {
        Shape out = {0};
        float stol = tol;
        if (L->type != L_TEXT && st[P_DRAW] < 1) {
            /* the dash: the first `len * draw` units along the outline, in order */
            float budget = len * st[P_DRAW];
            for (int i = 0; i < sh.n && budget > 0; i++) {
                Poly part;
                float left = poly_trim(&sh.v[i], budget, &part);
                stroke_poly(&out, &part, st[P_STROKEW], stol);
                free(part.p);
                budget = left;
            }
        } else {
            for (int i = 0; i < sh.n; i++) stroke_poly(&out, &sh.v[i], st[P_STROKEW], stol);
        }
        shape_to_segs(&out, &m, &pr->stroke);
        premul(stroke, opacity, pr->strokec);
        shape_free(&out);
    }
    shape_free(&sh);
    if (pr->isolate) {
        const Segs *g[2] = {&pr->fill, &pr->stroke};
        int any = 0;
        float x0 = 0, x1 = 0, y0 = 0, y1 = 0;
        for (int i = 0; i < 2; i++) {
            if (!g[i]->n) continue;
            if (!any) { x0 = g[i]->minx; x1 = g[i]->maxx; y0 = g[i]->miny; y1 = g[i]->maxy; any = 1; }
            else { x0 = fminf(x0, g[i]->minx); x1 = fmaxf(x1, g[i]->maxx); y0 = fminf(y0, g[i]->miny); y1 = fmaxf(y1, g[i]->maxy); }
        }
        if (!any) { pr->isolate = 0; return; }
        pr->bx0 = (int)floorf(x0) - 2; pr->bx1 = (int)ceilf(x1) + 2; pr->by0 = (int)floorf(y0) - 2; pr->by1 = (int)ceilf(y1) + 2;
    }
}

static void prepared_free(Prepared *pr) { free(pr->fill.s); free(pr->stroke.s); memset(pr, 0, sizeof(*pr)); }

/* Draw a prepared layer into rows [y0, y1). Text strokes go under the fill, like strokeText then fillText. */
static void draw_prepared(Canvas *cv, const Layer *L, Prepared *pr, int y0, int y1, float *acc, size_t acc_cap) {
    if (!pr->visible) return;
    if (pr->kind == 1) {
        draw_picture(cv, &pr->m, pr->levels, pr->nlevels, pr->bw, pr->bh, pr->radius, pr->cover, pr->opacity, y0, y1);
        return;
    }
    if (L->type == L_TEXT) {
        fill_segs(cv, &pr->stroke, pr->strokec, y0, y1, acc, acc_cap);
        fill_segs(cv, &pr->fill, pr->fillc, y0, y1, acc, acc_cap);
    } else {
        fill_segs(cv, &pr->fill, pr->fillc, y0, y1, acc, acc_cap);
        fill_segs(cv, &pr->stroke, pr->strokec, y0, y1, acc, acc_cap);
    }
}

/* ── worker pool ───────────────────────────────────────────────────────────── */

typedef void (*TaskFn)(void *ctx, int index, int worker);

typedef struct {
    int nthreads;
    TaskFn fn; void *ctx; int count;
    volatile long next, done;
    int generation, stop;
#ifdef _WIN32
    CRITICAL_SECTION lock; CONDITION_VARIABLE wake, finished;
    HANDLE *threads;
#else
    pthread_mutex_t lock; pthread_cond_t wake, finished;
    pthread_t *threads;
#endif
} Pool;

typedef struct { Pool *pool; int id; } WorkerArg;

static int pool_take(Pool *p) {
#ifdef _WIN32
    return (int)InterlockedIncrement(&p->next) - 1;
#else
    return (int)__sync_fetch_and_add(&p->next, 1);
#endif
}

static void pool_run_tasks(Pool *p, int worker) {
    for (;;) {
        int i = pool_take(p);
        if (i >= p->count) break;
        p->fn(p->ctx, i, worker);
#ifdef _WIN32
        if (InterlockedIncrement(&p->done) == p->count) { EnterCriticalSection(&p->lock); WakeAllConditionVariable(&p->finished); LeaveCriticalSection(&p->lock); }
#else
        if (__sync_add_and_fetch(&p->done, 1) == p->count) { pthread_mutex_lock(&p->lock); pthread_cond_broadcast(&p->finished); pthread_mutex_unlock(&p->lock); }
#endif
    }
}

#ifdef _WIN32
static DWORD WINAPI worker_main(LPVOID arg) {
#else
static void *worker_main(void *arg) {
#endif
    WorkerArg *wa = arg;
    Pool *p = wa->pool;
    int seen = 0;
    for (;;) {
#ifdef _WIN32
        EnterCriticalSection(&p->lock);
        while (p->generation == seen && !p->stop) SleepConditionVariableCS(&p->wake, &p->lock, INFINITE);
        seen = p->generation;
        int stop = p->stop;
        LeaveCriticalSection(&p->lock);
#else
        pthread_mutex_lock(&p->lock);
        while (p->generation == seen && !p->stop) pthread_cond_wait(&p->wake, &p->lock);
        seen = p->generation;
        int stop = p->stop;
        pthread_mutex_unlock(&p->lock);
#endif
        if (stop) break;
        pool_run_tasks(p, wa->id);
    }
#ifdef _WIN32
    return 0;
#else
    return NULL;
#endif
}

static void pool_init(Pool *p, int n) {
    memset(p, 0, sizeof(*p));
    p->nthreads = n;
#ifdef _WIN32
    InitializeCriticalSection(&p->lock); InitializeConditionVariable(&p->wake); InitializeConditionVariable(&p->finished);
    p->threads = xcalloc((size_t)n, sizeof(HANDLE));
#else
    pthread_mutex_init(&p->lock, NULL); pthread_cond_init(&p->wake, NULL); pthread_cond_init(&p->finished, NULL);
    p->threads = xcalloc((size_t)n, sizeof(pthread_t));
#endif
    for (int i = 1; i < n; i++) { /* worker 0 is the calling thread */
        WorkerArg *wa = xmalloc(sizeof(WorkerArg));
        wa->pool = p; wa->id = i;
#ifdef _WIN32
        p->threads[i] = CreateThread(NULL, 0, worker_main, wa, 0, NULL);
#else
        pthread_create(&p->threads[i], NULL, worker_main, wa);
#endif
    }
}

/* Run fn(ctx, 0..count-1) on all workers and wait for every task. */
static void pool_for(Pool *p, int count, TaskFn fn, void *ctx) {
    if (count <= 0) return;
    if (p->nthreads <= 1 || count == 1) { for (int i = 0; i < count; i++) fn(ctx, i, 0); return; }
#ifdef _WIN32
    EnterCriticalSection(&p->lock);
#else
    pthread_mutex_lock(&p->lock);
#endif
    p->fn = fn; p->ctx = ctx; p->count = count; p->next = 0; p->done = 0; p->generation++;
#ifdef _WIN32
    WakeAllConditionVariable(&p->wake);
    LeaveCriticalSection(&p->lock);
#else
    pthread_cond_broadcast(&p->wake);
    pthread_mutex_unlock(&p->lock);
#endif
    pool_run_tasks(p, 0);
#ifdef _WIN32
    EnterCriticalSection(&p->lock);
    while (p->done < p->count) SleepConditionVariableCS(&p->finished, &p->lock, INFINITE);
    LeaveCriticalSection(&p->lock);
#else
    pthread_mutex_lock(&p->lock);
    while (p->done < p->count) pthread_cond_wait(&p->finished, &p->lock);
    pthread_mutex_unlock(&p->lock);
#endif
}

/* ── frame rendering ───────────────────────────────────────────────────────── */

typedef struct {
    Scene *sc;
    Canvas *cv, *scratch;
    Prepared *pr;
    int layer;
    int bands, band_h;
    float **acc; size_t acc_cap;
    float radius; int pass; /* blur */
} Job;

static void job_band_range(Job *j, int band, int *y0, int *y1) {
    *y0 = band * j->band_h;
    *y1 = *y0 + j->band_h;
    if (*y1 > j->cv->h) *y1 = j->cv->h;
}

static void task_clear(void *ctx, int band, int worker) {
    Job *j = ctx;
    (void)worker;
    int y0, y1;
    job_band_range(j, band, &y0, &y1);
    Canvas *c = j->pass ? j->scratch : j->cv;
    const float *bg = j->sc->bg;
    float a = clampf(bg[3], 0, 1);
    for (int y = y0; y < y1; y++) {
        float *row = c->px + (size_t)y * c->w * 4;
        for (int x = 0; x < c->w; x++) {
            if (j->pass) { row[x * 4] = row[x * 4 + 1] = row[x * 4 + 2] = row[x * 4 + 3] = 0; }
            else { row[x * 4] = bg[0] * a; row[x * 4 + 1] = bg[1] * a; row[x * 4 + 2] = bg[2] * a; row[x * 4 + 3] = a; }
        }
    }
}

static void task_layer(void *ctx, int band, int worker) {
    Job *j = ctx;
    int y0, y1;
    job_band_range(j, band, &y0, &y1);
    Canvas *target = j->pr[j->layer].blur > 0 || j->pr[j->layer].isolate ? j->scratch : j->cv;
    draw_prepared(target, &j->sc->layers[j->layer], &j->pr[j->layer], y0, y1, j->acc[worker], j->acc_cap);
}

/* Box blur rows (pass 0) or columns (pass 1) of the scratch canvas; three runs ≈ gaussian. */
static void box_line(float *line, int n, int stride, int r, float *tmp) {
    if (r < 1 || n < 2) return;
    for (int c = 0; c < 4; c++) {
        double sum = 0;
        int w = 2 * r + 1;
        for (int i = -r; i <= r; i++) { int k = i < 0 ? 0 : i >= n ? n - 1 : i; sum += line[(size_t)k * stride + c]; }
        for (int i = 0; i < n; i++) {
            tmp[i] = (float)(sum / w);
            int add = i + r + 1, sub = i - r;
            add = add >= n ? n - 1 : add;
            sub = sub < 0 ? 0 : sub;
            sum += line[(size_t)add * stride + c] - line[(size_t)sub * stride + c];
        }
        for (int i = 0; i < n; i++) line[(size_t)i * stride + c] = tmp[i];
    }
}

static int blur_radii(float sigma, int *r) {
    /* three box sizes for a gaussian of this sigma (Kovesi) */
    double wideal = sqrt(12 * sigma * sigma / 3 + 1);
    int wl = (int)floor(wideal);
    if (wl % 2 == 0) wl--;
    int wu = wl + 2;
    double mideal = (12 * sigma * sigma - 3 * wl * wl - 12 * wl - 9) / (-4.0 * wl - 4);
    int m = (int)lround(mideal);
    for (int i = 0; i < 3; i++) r[i] = ((i < m ? wl : wu) - 1) / 2;
    return 3;
}

static void task_blur(void *ctx, int index, int worker) {
    Job *j = ctx;
    Canvas *c = j->scratch;
    float *tmp = j->acc[worker];
    int r[3];
    blur_radii(j->radius, r);
    if (j->pass == 0) { /* rows */
        float *line = c->px + (size_t)index * c->w * 4;
        for (int k = 0; k < 3; k++) box_line(line, c->w, 4, r[k], tmp);
    } else {            /* columns */
        float *line = c->px + (size_t)index * 4;
        for (int k = 0; k < 3; k++) box_line(line, c->h, c->w * 4, r[k], tmp);
    }
}

/* Colour, key, LUT, mask and wipes on the layer in the scratch canvas (premultiplied in, premultiplied out). */
static void task_effects(void *ctx, int band, int worker) {
    Job *j = ctx;
    (void)worker;
    const Prepared *pr = &j->pr[j->layer];
    Canvas *c = j->scratch;
    int y0, y1;
    job_band_range(j, band, &y0, &y1);
    if (y0 < pr->by0) y0 = pr->by0;
    if (y1 > pr->by1) y1 = pr->by1;
    int x0 = pr->bx0 < 0 ? 0 : pr->bx0, x1 = pr->bx1 > c->w ? c->w : pr->bx1;
    int masked = pr->L->has_mask || pr->nwipe > 0;
    for (int y = y0 < 0 ? 0 : y0; y < y1; y++) {
        float *p = c->px + ((size_t)y * c->w + x0) * 4;
        for (int x = x0; x < x1; x++, p += 4) {
            float a = p[3];
            if (a <= 1e-6f) continue;
            float rgb[3] = {p[0] / a, p[1] / a, p[2] / a};
            effect_pixel(pr, rgb, &a);
            if (masked) {
                Pt l = mat_apply(&pr->inv, x + 0.5f, y + 0.5f);
                a *= coverage(pr, l.x, l.y);
            }
            p[0] = rgb[0] * a; p[1] = rgb[1] * a; p[2] = rgb[2] * a; p[3] = a;
        }
    }
}

static void task_composite(void *ctx, int band, int worker) {
    Job *j = ctx;
    (void)worker;
    int y0, y1;
    job_band_range(j, band, &y0, &y1);
    for (int y = y0; y < y1; y++) {
        float *d = j->cv->px + (size_t)y * j->cv->w * 4, *s = j->scratch->px + (size_t)y * j->cv->w * 4;
        for (int x = 0; x < j->cv->w; x++, d += 4, s += 4) {
            float inv = 1 - s[3];
            d[0] = s[0] + d[0] * inv; d[1] = s[1] + d[1] * inv; d[2] = s[2] + d[2] * inv; d[3] = s[3] + d[3] * inv;
        }
    }
}

static void render_frame(Scene *sc, Pool *pool, Job *job, double t) {
    job->pass = 0;
    pool_for(pool, job->bands, task_clear, job);
    for (int i = 0; i < sc->nlayers; i++) {
        Prepared *pr = &job->pr[i];
        Mat global = {(double)job->cv->w / sc->w, 0, 0, (double)job->cv->h / sc->h, 0, 0};
        prepare_layer(sc, &sc->layers[i], t, &global, pr);
        if (!pr->visible) continue;
        job->layer = i;
        if (pr->blur > 0 || pr->isolate) {
            job->pass = 1;
            pool_for(pool, job->bands, task_clear, job);
            job->pass = 0;
            pool_for(pool, job->bands, task_layer, job);
            if (pr->isolate) pool_for(pool, job->bands, task_effects, job);
            if (pr->blur > 0) {
                job->radius = pr->blur;
                job->pass = 0; pool_for(pool, job->cv->h, task_blur, job);
                job->pass = 1; pool_for(pool, job->cv->w, task_blur, job);
            }
            job->pass = 0;
            pool_for(pool, job->bands, task_composite, job);
        } else {
            pool_for(pool, job->bands, task_layer, job);
        }
        prepared_free(pr);
    }
}

static void to_rgba8(const Canvas *cv, unsigned char *out) {
    size_t n = (size_t)cv->w * cv->h;
    for (size_t i = 0; i < n; i++) {
        const float *p = cv->px + i * 4;
        float a = p[3];
        /* over an opaque background the result is opaque; un-premultiply what is not */
        float k = a > 1e-6f && a < 0.999f ? 1.0f / a : 1.0f;
        for (int c = 0; c < 3; c++) { float v = p[c] * k; out[i * 4 + c] = (unsigned char)(clampf(v, 0, 1) * 255.0f + 0.5f); }
        out[i * 4 + 3] = (unsigned char)(clampf(a, 0, 1) * 255.0f + 0.5f);
    }
}

/* ── settings: threads and memory ──────────────────────────────────────────── */

static int cpu_count(void) {
#ifdef _WIN32
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    return (int)si.dwNumberOfProcessors;
#else
    long n = sysconf(_SC_NPROCESSORS_ONLN);
    return n > 0 ? (int)n : 1;
#endif
}

typedef struct { int threads; double mem_mb; double scale; int cols; char *times; char *out; } Opts;

static void parse_opts(int argc, char **argv, int from, Opts *o) {
    memset(o, 0, sizeof(*o));
    o->scale = 1; o->cols = 0;
    for (int i = from; i < argc; i++) {
        if (!strcmp(argv[i], "--threads") && i + 1 < argc) o->threads = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--mem") && i + 1 < argc) o->mem_mb = atof(argv[++i]);
        else if (!strcmp(argv[i], "--scale") && i + 1 < argc) o->scale = atof(argv[++i]);
        else if (!strcmp(argv[i], "--cols") && i + 1 < argc) o->cols = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--times") && i + 1 < argc) o->times = argv[++i];
        else if (!o->out && argv[i][0] != '-') o->out = argv[i];
    }
    if (o->threads <= 0) o->threads = cpu_count();
    if (o->threads > 256) o->threads = 256;
}

static double image_bytes(const Scene *sc) {
    double b = 0;
    for (int i = 0; i < sc->nimages; i++) for (int l = 0; l < sc->images[i].levels; l++) b += (double)sc->images[i].lv[l].w * sc->images[i].lv[l].h * 4;
    for (int i = 0; i < sc->nvideos; i++) b += (double)sc->videos[i].w * sc->videos[i].h * 4;
    return b;
}

/* Does any layer need the second canvas (blur, or effects drawn on the layer alone)? */
static int has_blur(const Scene *sc) {
    for (int i = 0; i < sc->nlayers; i++) {
        const Layer *L = &sc->layers[i];
        if (L->stat[P_BLUR] > 0 || L->num_keys[P_BLUR] || L->has_chroma || L->lut || L->has_mask) return 1;
        for (int p = P_BRIGHT; p <= P_TEMP; p++) if (L->stat[p] != 0 || L->num_keys[p]) return 1;
        if (L->tin.type >= T_BLUR || L->tout.type >= T_BLUR) return 1; /* blur, wipes, iris */
    }
    return 0;
}

/* Set up buffers for a W x H output, fitting the threads into the memory cap. */
static void setup(Scene *sc, Opts *o, int W, int H, Canvas *cv, Canvas *scratch, Job *job, Pool *pool, double extra_bytes) {
    double frame = (double)W * H * 4 * sizeof(float);
    double fixed = frame + (has_blur(sc) ? frame : 0) + image_bytes(sc) + extra_bytes;
    int threads = o->threads;
    int bands = threads * 2;
    double per_thread = 0;
    for (;;) {
        bands = threads * 2;
        int band_h = (H + bands - 1) / bands;
        size_t cap = (size_t)(W + 2) * (size_t)(band_h + 2);
        if ((size_t)(H > W ? H : W) > cap) cap = (size_t)(H > W ? H : W);
        per_thread = (double)cap * sizeof(float);
        double total = fixed + per_thread * threads;
        if (o->mem_mb <= 0 || total <= o->mem_mb * 1024 * 1024) break;
        if (threads == 1)
            die(3, "needs about %.0f MB, more than --mem %.0f MB allows (raise STUDIO_MEM_MB, or lower the size)",
                total / (1024 * 1024), o->mem_mb);
        threads--;
    }
    fprintf(stderr, "sqengine: %dx%d, %d thread(s), about %.0f MB\n", W, H, threads,
            (fixed + per_thread * threads) / (1024 * 1024));
    cv->w = W; cv->h = H; cv->px = xmalloc((size_t)W * H * 4 * sizeof(float));
    scratch->w = W; scratch->h = H; scratch->px = has_blur(sc) ? xmalloc((size_t)W * H * 4 * sizeof(float)) : NULL;
    memset(job, 0, sizeof(*job));
    job->sc = sc; job->cv = cv; job->scratch = scratch;
    job->pr = xcalloc((size_t)(sc->nlayers ? sc->nlayers : 1), sizeof(Prepared));
    job->bands = bands;
    job->band_h = (H + bands - 1) / bands;
    job->acc_cap = (size_t)(per_thread / sizeof(float));
    job->acc = xmalloc(sizeof(float *) * (size_t)threads);
    for (int i = 0; i < threads; i++) job->acc[i] = xmalloc(job->acc_cap * sizeof(float));
    pool_init(pool, threads);
}

/* ── modes ─────────────────────────────────────────────────────────────────── */

static int mode_video(Scene *sc, Opts *o) {
#ifdef _WIN32
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    Canvas cv, scratch;
    Job job;
    Pool pool;
    int W = sc->out_w > 0 ? sc->out_w : sc->w, H = sc->out_h > 0 ? sc->out_h : sc->h;
    double out_bytes = (double)W * H * 4;
    setup(sc, o, W, H, &cv, &scratch, &job, &pool, out_bytes);
    unsigned char *out = xmalloc((size_t)W * H * 4);
    long frames = (long)llround(sc->duration * sc->fps);
    if (frames < 1) frames = 1;
    for (long f = 0; f < frames; f++) {
        double t = f / sc->fps;
        render_frame(sc, &pool, &job, t);
        to_rgba8(&cv, out);
        if (fwrite(out, 1, (size_t)W * H * 4, stdout) != (size_t)W * H * 4)
            die(4, "the encoder stopped reading (frame %ld)", f);
        fprintf(stderr, "frame %ld %ld\n", f + 1, frames);
        fflush(stderr);
    }
    fflush(stdout);
    return 0;
}

static int mode_still(Scene *sc, Opts *o) {
    if (!o->out) die(2, "still needs an output .png path");
    double times[256];
    int nt = 0;
    if (o->times) {
        char *copy = xstrdup(o->times), *tok = strtok(copy, ",");
        while (tok && nt < 256) { times[nt++] = atof(tok); tok = strtok(NULL, ","); }
        free(copy);
    }
    if (!nt) times[nt++] = 0;
    int W = (int)lround(sc->w * o->scale), H = (int)lround(sc->h * o->scale);
    if (W < 1) W = 1;
    if (H < 1) H = 1;
    int cols = o->cols > 0 ? o->cols : (nt < 4 ? nt : 4);
    int rows = (nt + cols - 1) / cols;
    int gap = nt > 1 ? 8 : 0;
    int SW = cols * W + (cols - 1) * gap, SH = rows * H + (rows - 1) * gap;
    Canvas cv, scratch;
    Job job;
    Pool pool;
    setup(sc, o, W, H, &cv, &scratch, &job, &pool, (double)SW * SH * 4);
    unsigned char *sheet = xcalloc((size_t)SW * SH * 4, 1);
    for (size_t i = 0; i < (size_t)SW * SH; i++) { sheet[i * 4 + 0] = 24; sheet[i * 4 + 1] = 24; sheet[i * 4 + 2] = 24; sheet[i * 4 + 3] = 255; }
    unsigned char *one = xmalloc((size_t)W * H * 4);
    for (int i = 0; i < nt; i++) {
        render_frame(sc, &pool, &job, times[i]);
        to_rgba8(&cv, one);
        int ox = (i % cols) * (W + gap), oy = (i / cols) * (H + gap);
        for (int y = 0; y < H; y++) memcpy(sheet + ((size_t)(oy + y) * SW + ox) * 4, one + (size_t)y * W * 4, (size_t)W * 4);
    }
    if (!stbi_write_png(o->out, SW, SH, 4, sheet, SW * 4)) die(4, "cannot write %s", o->out);
    fprintf(stderr, "sqengine: wrote %s (%dx%d, %d frame%s)\n", o->out, SW, SH, nt, nt == 1 ? "" : "s");
    return 0;
}

static void write_le16(FILE *f, int v) { fputc(v & 255, f); fputc((v >> 8) & 255, f); }
static void write_le32(FILE *f, long v) { write_le16(f, (int)(v & 0xffff)); write_le16(f, (int)((v >> 16) & 0xffff)); }

static int mode_audio(Scene *sc) {
    if (!sc->audio_out) die(2, "scene has no audio line");
    int rate = sc->audio_rate > 0 ? sc->audio_rate : 48000;
    long total = (long)ceil(sc->duration * rate);
    if (total < 1) total = 1;
    float *mix = xcalloc((size_t)total * 2, sizeof(float));
    size_t chunk = 4096;
    float *buf = xmalloc(chunk * 2 * sizeof(float));
    for (int c = 0; c < sc->nclips; c++) {
        AClip *cl = &sc->clips[c];
        FILE *p = popen(cl->cmd, PIPE_READ);
        if (!p) { fprintf(stderr, "sqengine: cannot start audio decoder %d\n", c); continue; }
        double clip_end = fmin(cl->start + cl->length, sc->duration);
        long pos = (long)llround(cl->start * rate), end = (long)llround(clip_end * rate);
        for (;;) {
            size_t got = fread(buf, sizeof(float) * 2, chunk, p);
            if (!got) break;
            for (size_t i = 0; i < got && pos < end; i++, pos++) {
                if (pos < 0) continue;
                double t = (double)pos / rate, local = t - cl->start;
                double v = track_num(cl->keys.n ? &cl->keys : NULL, t, (float)cl->volume);
                if (cl->fade_in > 0 && local < cl->fade_in) v *= local / cl->fade_in;
                if (cl->fade_out > 0 && clip_end - t < cl->fade_out) v *= fmax(0, (clip_end - t) / cl->fade_out);
                if (v < 0) v = 0;
                mix[pos * 2] += (float)(buf[i * 2] * v);
                mix[pos * 2 + 1] += (float)(buf[i * 2 + 1] * v);
            }
            if (pos >= end) break;
        }
        pclose(p);
        fprintf(stderr, "audio %d %d\n", c + 1, sc->nclips);
    }
    FILE *f = fopen(sc->audio_out, "wb");
    if (!f) die(4, "cannot write %s", sc->audio_out);
    long data = total * 2 * 2;
    fwrite("RIFF", 1, 4, f); write_le32(f, 36 + data); fwrite("WAVEfmt ", 1, 8, f);
    write_le32(f, 16); write_le16(f, 1); write_le16(f, 2); write_le32(f, rate); write_le32(f, (long)rate * 4);
    write_le16(f, 4); write_le16(f, 16); fwrite("data", 1, 4, f); write_le32(f, data);
    for (long i = 0; i < total * 2; i++) {
        float v = clampf(mix[i], -1, 1);
        write_le16(f, (int)(int16_t)lrintf(v < 0 ? v * 32768.0f : v * 32767.0f));
    }
    fclose(f);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2 || !strcmp(argv[1], "--help")) {
        fprintf(stderr, "usage: sqengine video|still|audio SCENE [OUT.png] [--threads N] [--mem MB] [--times a,b] [--scale S] [--cols C]\n"
                        "       sqengine info\n");
        return argc < 2 ? 2 : 0;
    }
    if (!strcmp(argv[1], "info")) { printf("sqengine %s cpus=%d\n", SQ_VERSION, cpu_count()); return 0; }
    if (argc < 3) die(2, "missing scene file");
    Scene sc;
    load_scene(&sc, argv[2]);
    Opts o;
    parse_opts(argc, argv, 3, &o);
    if (!strcmp(argv[1], "video")) return mode_video(&sc, &o);
    if (!strcmp(argv[1], "still")) return mode_still(&sc, &o);
    if (!strcmp(argv[1], "audio")) return mode_audio(&sc);
    die(2, "unknown mode %s", argv[1]);
    return 2;
}
