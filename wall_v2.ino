// wall_v2 — 25×25 LED grid
// Teensy 3.6 + OctoWS2811 adapter + WS2812B LEDs

#define USE_OCTOWS2811
#include <OctoWS2811.h>
#include <FastLED.h>
#include <Encoder.h>
#include <Audio.h>
#include <Wire.h>
#include <SPI.h>

// ── Grid ──────────────────────────────────────────────────────────
#define MATRIX_W       25
#define MATRIX_H       25
#define GRID_LEDS      (MATRIX_W * MATRIX_H)
#define LEDS_PER_STRIP 100
#define NUM_STRIPS     8
#define NUM_LEDS       (LEDS_PER_STRIP * NUM_STRIPS)

CRGB leds[NUM_LEDS];

// ── Global tunables ───────────────────────────────────────────────
uint8_t  brightness = 128;   // 0–255
uint8_t  speedScale = 128;   // 128 = 1×; 64 = 0.5×; 255 ≈ 2×
bool     autoCycle  = true;

// ── Encoder & button ──────────────────────────────────────────────
// Encoder library (bundled with Teensyduino) uses hardware interrupts
// on pins 0 and 1 for glitch-free counting at any rotation speed.
#define PIN_ENC_A           0   // interrupt-capable
#define PIN_ENC_B           1   // interrupt-capable
#define PIN_ENC_BTN         3
#define ENC_COUNTS_PER_STEP 4   // typical EC11; set to 2 for some encoders

enum EncMode   { MODE_ANIM, MODE_BRIGHT };
enum InputEvent { NEXT_ANIM, PREV_ANIM, BRIGHT_UP, BRIGHT_DOWN, SPEED_UP, SPEED_DOWN, TOGGLE_CYCLE };
EncMode encMode = MODE_ANIM;
Encoder enc(PIN_ENC_A, PIN_ENC_B);

// ── Audio pipeline ─────────────────────────────────────────────────
// Analog mic (MAX4466, KY-038, etc.) on A2 → 1024-point FFT.
// No Audio Shield needed; AudioInputAnalog drives the ADC directly.
// Call AudioMemory(12) in setup() to allocate DMA buffers.
AudioInputAnalog    micIn(A2);
AudioAnalyzeFFT1024 fft1024;
AudioConnection     patchAudio(micIn, fft1024);

// Tuning constants — adjust if bars are too small/large or beats misfire
#define SPEC_SENSITIVITY  6.0f   // scale FFT output to bar height
#define BEAT_THRESHOLD    2.2f   // energy must exceed avg × this
#define BEAT_MIN_LEVEL    0.08f  // ignore quiet noise below this level

// ── XY mapping ────────────────────────────────────────────────────
// Physical layout:
//   Strips 0–6: 3 rows each (75 of 100 slots used), rows 0–20.
//   Strip  7:   4 rows (rows 21–24, all 100 slots used).
// Serpentine: rows where y%3==1, and the final row, run right-to-left.
uint16_t XY(uint8_t x, uint8_t y) {
    if (x >= MATRIX_W || y >= MATRIX_H) return 0;
    uint8_t strip    = (y < 21) ? y / 3 : 7;
    uint8_t stripRow = (y < 21) ? y % 3 : y - 21;
    bool    reversed = (y % 3 == 1) || (y == MATRIX_H - 1);
    uint8_t col      = reversed ? (MATRIX_W - 1 - x) : x;
    return (uint16_t)strip * LEDS_PER_STRIP + stripRow * MATRIX_W + col;
}

// ── Frame timing ──────────────────────────────────────────────────
// Returns true when it's time to render the next frame.
// speedScale > 128 shortens the interval (faster); < 128 lengthens it (slower).
bool frameReady(uint32_t now, uint32_t &last, uint16_t baseMs) {
    uint16_t ms = (uint32_t)baseMs * 128 / max((uint8_t)1, speedScale);
    if (now - last < ms) return false;
    last = now;
    return true;
}

// ── Animation state (global to avoid stack pressure) ──────────────

static uint8_t  heat[MATRIX_W][MATRIX_H];

static float    rdA[MATRIX_W][MATRIX_H];
static float    rdB[MATRIX_W][MATRIX_H];
static float    rdA2[MATRIX_W][MATRIX_H];
static float    rdB2[MATRIX_W][MATRIX_H];

static uint8_t  gol[MATRIX_W][MATRIX_H];      // 0 = dead; 1–255 = age
static uint8_t  golNext[MATRIX_W][MATRIX_H];

#define VORONOI_SEEDS 6
struct VoroSeed { float cx, cy, rx, ry, fx, fy, px, py; };
static VoroSeed   voroSeeds[VORONOI_SEEDS];
static uint32_t   voroTick    = 0;
static uint8_t    voroBaseHue = 0;

#define NUM_BOIDS 16
struct Boid { float x, y, vx, vy; uint8_t hue; };
static Boid boids[NUM_BOIDS];

static uint16_t wavePhase[4];

static uint8_t  specPeak[MATRIX_W];       // per-column peak hold height
static uint8_t  specPeakTimer[MATRIX_W];  // frames until peak starts falling

// ── Draw mode (pushed from app) ───────────────────────────────────
bool     drawMode  = false;
bool     frameRecv = false;    // true while accumulating a binary 'F' frame
uint16_t framePos  = 0;
uint8_t  frameBuf[GRID_LEDS * 3];  // 1875 bytes; row-major RGB

// ── Animations ────────────────────────────────────────────────────

// 1. Plasma ──────────────────────────────────────────────────────
// Wave interference in x, y, and diagonal drives brightness; hue
// stays within a narrow band of a slowly drifting base color.
void drawPlasma(uint32_t now) {
    static uint32_t last = 0;
    static uint16_t t    = 0;
    if (!frameReady(now, last, 18)) return;
    uint8_t baseHue = t >> 4;
    uint16_t t2 = t >> 1;           // half speed
    uint16_t t3 = t + (t >> 1);     // 1.5× speed (incommensurate with t and t2)
    for (uint8_t y = 0; y < MATRIX_H; y++) {
        for (uint8_t x = 0; x < MATRIX_W; x++) {
            uint8_t w1 = sin8(x *  9 + t);
            uint8_t w2 = sin8(y * 11 - t);
            // w3 is phase-warped by w1 — sin(sin(...)) breaks the tile structure
            uint8_t w3 = sin8((x + y) * 6 + (w1 >> 3) + t2);
            // w4 adds an off-axis rhythm at a third independent speed
            uint8_t w4 = sin8(x * 4 - y * 7 + t3);
            uint8_t wave = scale8(w1, 60) + scale8(w2, 60)
                         + scale8(w3, 68) + scale8(w4, 67);
            leds[XY(x, y)] = CHSV(baseHue + (wave >> 3), 230, wave);
        }
    }
    FastLED.show();
    t += 3;
}

// 2. Ripple ──────────────────────────────────────────────────────
// Concentric rings expand from the center; hue drifts slowly.
void drawRipple(uint32_t now) {
    static uint32_t last = 0;
    static uint16_t t    = 0;
    if (!frameReady(now, last, 20)) return;
    for (uint8_t y = 0; y < MATRIX_H; y++)
        for (uint8_t x = 0; x < MATRIX_W; x++) {
            int8_t  dx   = (int8_t)x - MATRIX_W / 2;
            int8_t  dy   = (int8_t)y - MATRIX_H / 2;
            uint8_t dist = (uint8_t)(sqrtf((float)(dx*dx + dy*dy)) * 20.0f);
            uint8_t wave = sin8(dist - (uint8_t)t);
            leds[XY(x, y)] = CHSV((uint8_t)(t >> 2) + 160, 230, wave);
        }
    FastLED.show();
    t += 4;
}

// 3. Twinkle ─────────────────────────────────────────────────────
// Stars blink on then fade; new ones only ignite on dark pixels.
void drawTwinkle(uint32_t now) {
    static uint32_t last = 0;
    if (!frameReady(now, last, 35)) return;
    for (uint16_t i = 0; i < NUM_LEDS; i++)
        leds[i].fadeToBlackBy(10);
    for (uint8_t i = 0; i < 4; i++) {
        uint8_t x = random8(MATRIX_W), y = random8(MATRIX_H);
        if (!leds[XY(x, y)])
            leds[XY(x, y)] = CHSV(random8(), 160 + random8(95), 255);
    }
    FastLED.show();
}

// 4. Fire ────────────────────────────────────────────────────────
// Heat rises from the bottom; aggressive cooling creates a steep
// gradient: yellow-white at the base, red mid-way, dark at the top.
void initFire() { memset(heat, 0, sizeof(heat)); }

void drawFire(uint32_t now) {
    static uint32_t last = 0;
    if (!frameReady(now, last, 25)) return;

    // Vertical: cool, drift upward, stoke base
    for (uint8_t x = 0; x < MATRIX_W; x++) {
        for (uint8_t y = 0; y < MATRIX_H; y++)
            heat[x][y] = qsub8(heat[x][y], random8(5, 15));
        for (uint8_t y = MATRIX_H - 1; y >= 2; y--)
            heat[x][y] = (heat[x][y-1] + heat[x][y-2] + heat[x][y-2]) / 3;
        heat[x][0] = qadd8(heat[x][0], random8(80, 180));
    }

    // Horizontal: blend each row with its neighbours so columns
    // interact and the flame looks unified rather than striped.
    static uint8_t rowBuf[MATRIX_W];
    for (uint8_t y = 0; y < MATRIX_H; y++) {
        for (uint8_t x = 0; x < MATRIX_W; x++) {
            uint8_t l = heat[(x + MATRIX_W - 1) % MATRIX_W][y];
            uint8_t r = heat[(x + 1)            % MATRIX_W][y];
            rowBuf[x] = (heat[x][y] * 2 + l + r) / 4;
        }
        for (uint8_t x = 0; x < MATRIX_W; x++) heat[x][y] = rowBuf[x];
    }

    for (uint8_t x = 0; x < MATRIX_W; x++)
        for (uint8_t y = 0; y < MATRIX_H; y++)
            leds[XY(x, MATRIX_H - 1 - y)] = HeatColor(heat[x][y]);
    FastLED.show();
}

// 5. Wave Interference ────────────────────────────────────────────
// 4 point sources drift on independent Lissajous paths and each emit
// radial sine waves at slightly different speeds. Constructive
// interference lights up; destructive interference goes dark.
void initWaveInterference() { memset(wavePhase, 0, sizeof(wavePhase)); }

void drawWaveInterference(uint32_t now) {
    static uint32_t last = 0;
    static uint32_t tick = 0;
    if (!frameReady(now, last, 22)) return;

    float T = tick * 0.004f;
    float srcX[4] = {
        12 + 10 * cosf(T * 1.00f),
        12 + 10 * cosf(T * 0.70f + 1.0f),
        12 +  8 * cosf(T * 1.40f + 3.0f),
        12 +  9 * cosf(T * 0.90f + 4.5f),
    };
    float srcY[4] = {
        12 + 10 * sinf(T * 1.30f),
        12 + 10 * sinf(T * 1.00f + 2.0f),
        12 +  8 * sinf(T * 0.80f + 1.5f),
        12 +  9 * sinf(T * 1.20f + 0.7f),
    };
    const uint8_t phaseStep[4] = {5, 7, 4, 6};

    for (uint8_t y = 0; y < MATRIX_H; y++) {
        for (uint8_t x = 0; x < MATRIX_W; x++) {
            int16_t sum = 0;
            for (uint8_t s = 0; s < 4; s++) {
                float   dx   = x - srcX[s];
                float   dy   = y - srcY[s];
                float   dist = sqrtf(dx*dx + dy*dy);
                // Subtract 128 so sin8 oscillates around 0 for true interference
                sum += (int16_t)sin8((uint8_t)(dist * 20.0f) + wavePhase[s]) - 128;
            }
            // sum: -512 to +508 → map to 0–255
            uint8_t val = (uint8_t)((sum + 512) >> 2);
            leds[XY(x, y)] = CHSV(val + (uint8_t)(tick >> 3), 220, val);
        }
    }
    for (uint8_t s = 0; s < 4; s++) wavePhase[s] += phaseStep[s];

    FastLED.show();
    tick++;
}

// 6. Reaction-Diffusion (Gray-Scott "coral") ──────────────────────
// Two chemical species A and B interact via:
//   dA/dt = Da∇²A  −  A·B²  +  f·(1−A)
//   dB/dt = Db∇²B  +  A·B²  −  (f+k)·B
// Self-organizing into coral/spot patterns. Pre-runs 300 steps on
// init so the pattern is already forming when the animation starts.

#define RD_DA           1.0f
#define RD_DB           0.5f
#define RD_F            0.054f
#define RD_K            0.063f
#define RD_STEPS_FRAME  20

void rdStep() {
    for (uint8_t x = 0; x < MATRIX_W; x++) {
        uint8_t xL = (x == 0)          ? MATRIX_W - 1 : x - 1;
        uint8_t xR = (x == MATRIX_W-1) ? 0            : x + 1;
        for (uint8_t y = 0; y < MATRIX_H; y++) {
            uint8_t yU = (y == 0)          ? MATRIX_H - 1 : y - 1;
            uint8_t yD = (y == MATRIX_H-1) ? 0            : y + 1;
            float a    = rdA[x][y], b = rdB[x][y];
            float lapA = rdA[xL][y] + rdA[xR][y] + rdA[x][yU] + rdA[x][yD] - 4*a;
            float lapB = rdB[xL][y] + rdB[xR][y] + rdB[x][yU] + rdB[x][yD] - 4*b;
            float ab2  = a * b * b;
            rdA2[x][y] = constrain(a + RD_DA*lapA - ab2 + RD_F*(1.0f-a), 0.0f, 1.0f);
            rdB2[x][y] = constrain(b + RD_DB*lapB + ab2 - (RD_F+RD_K)*b, 0.0f, 1.0f);
        }
    }
    memcpy(rdA, rdA2, sizeof(rdA));
    memcpy(rdB, rdB2, sizeof(rdB));
}

void initRD() {
    for (uint8_t x = 0; x < MATRIX_W; x++)
        for (uint8_t y = 0; y < MATRIX_H; y++) {
            rdA[x][y] = 1.0f; rdB[x][y] = 0.0f;
        }
    for (uint8_t i = 0; i < 5; i++) {
        uint8_t cx = 3 + random8(MATRIX_W - 6);
        uint8_t cy = 3 + random8(MATRIX_H - 6);
        for (int8_t dx = -2; dx <= 2; dx++)
            for (int8_t dy = -2; dy <= 2; dy++) {
                rdA[cx+dx][cy+dy] = 0.0f;
                rdB[cx+dx][cy+dy] = 1.0f;
            }
    }
    for (uint16_t i = 0; i < 300; i++) rdStep();
}

void drawRD(uint32_t now) {
    static uint32_t last = 0;
    if (!frameReady(now, last, 33)) return;
    for (uint8_t s = 0; s < RD_STEPS_FRAME; s++) rdStep();

    // Auto-scale: B peaks at ~0.3 with coral params, so a fixed *255 mapping
    // would compress everything into the bottom 30% of the palette (all dark).
    // Instead find the actual max each frame and stretch it to fill 0–255.
    float maxB = 0.05f;  // floor keeps background black until patterns emerge
    for (uint8_t x = 0; x < MATRIX_W; x++)
        for (uint8_t y = 0; y < MATRIX_H; y++)
            if (rdB[x][y] > maxB) maxB = rdB[x][y];
    float scale = 255.0f / maxB;

    for (uint8_t x = 0; x < MATRIX_W; x++)
        for (uint8_t y = 0; y < MATRIX_H; y++) {
            uint8_t v = (uint8_t)min(rdB[x][y] * scale, 255.0f);
            leds[XY(x, y)] = ColorFromPalette(HeatColors_p, v);
        }
    FastLED.show();
}

// 7. Game of Life (generational color) ────────────────────────────
// Conway's Life on a toroidal grid. Cells age 1–255; hue encodes age:
// young = blue, old = red. Reseeds when the board stagnates.
void initGoL() {
    for (uint8_t x = 0; x < MATRIX_W; x++)
        for (uint8_t y = 0; y < MATRIX_H; y++)
            gol[x][y] = (random8(3) == 0) ? 1 : 0;
}

void drawGoL(uint32_t now) {
    static uint32_t last      = 0;
    static uint16_t prevPop   = 0;
    static uint8_t  stagnant  = 0;
    if (!frameReady(now, last, 140)) return;

    uint16_t pop = 0;
    for (uint8_t x = 0; x < MATRIX_W; x++) {
        for (uint8_t y = 0; y < MATRIX_H; y++) {
            uint8_t n = 0;
            for (int8_t dx = -1; dx <= 1; dx++)
                for (int8_t dy = -1; dy <= 1; dy++) {
                    if (!dx && !dy) continue;
                    uint8_t nx = (x + dx + MATRIX_W) % MATRIX_W;
                    uint8_t ny = (y + dy + MATRIX_H) % MATRIX_H;
                    if (gol[nx][ny]) n++;
                }
            bool alive = gol[x][y] > 0;
            if      (alive && (n == 2 || n == 3)) golNext[x][y] = qadd8(gol[x][y], 1);
            else if (!alive && n == 3)             golNext[x][y] = 1;
            else                                   golNext[x][y] = 0;
            if (golNext[x][y]) pop++;
        }
    }
    memcpy(gol, golNext, sizeof(gol));

    stagnant = (pop == prevPop) ? stagnant + 1 : 0;
    prevPop  = pop;
    if (pop < 8 || stagnant > 25) { initGoL(); stagnant = 0; }

    for (uint8_t x = 0; x < MATRIX_W; x++)
        for (uint8_t y = 0; y < MATRIX_H; y++) {
            uint8_t age = gol[x][y];
            if (age) {
                // young = blue (hue 160), old = red (hue ~0)
                leds[XY(x, y)] = CHSV(160 - scale8(age, 160), 240, 220);
            } else {
                leds[XY(x, y)] = CRGB::Black;
            }
        }
    FastLED.show();
}

// 8. Voronoi Cells ────────────────────────────────────────────────
// 6 seeds trace Lissajous figures so each cell sweeps a flowing curve
// rather than bouncing in a straight line. Hues are evenly spaced and
// drift together — all cells shift colour in sync over ~30 s.
// Intra-cell brightness is flat (no distance gradient) so colours read
// clearly; boundaries stay bright white.

// Lissajous frequency pairs — irrational ratios keep patterns aperiodic
static const float voroFx[6] = {1.00f, 2.00f, 0.70f, 1.50f, 1.00f, 3.00f};
static const float voroFy[6] = {1.30f, 3.00f, 1.10f, 1.00f, 2.00f, 2.00f};

void initVoronoi() {
    voroTick    = 0;
    voroBaseHue = 0;
    float cx = MATRIX_W / 2.0f, cy = MATRIX_H / 2.0f;
    for (uint8_t i = 0; i < VORONOI_SEEDS; i++) {
        // Spread centres slightly so seeds don't all start together
        voroSeeds[i].cx = cx + (random8() - 128) * 0.04f;
        voroSeeds[i].cy = cy + (random8() - 128) * 0.04f;
        // Orbit radii: 8–10 px so seeds always stay well within the grid
        voroSeeds[i].rx = 8.0f + random8(3);
        voroSeeds[i].ry = 8.0f + random8(3);
        voroSeeds[i].fx = voroFx[i];
        voroSeeds[i].fy = voroFy[i];
        // Random phase offsets so seeds are spread around at t=0
        voroSeeds[i].px = i * 1.047f + random8() * 0.01f;   // 60° apart
        voroSeeds[i].py = i * 0.785f + random8() * 0.01f;   // 45° apart
    }
}

void drawVoronoi(uint32_t now) {
    static uint32_t last = 0;
    if (!frameReady(now, last, 33)) return;

    float T = voroTick * 0.0025f;   // one full orbit cycle ≈ 2500 frames ≈ 83 s
    voroTick++;
    voroBaseHue++;   // full colour-wheel cycle every 256 frames ≈ 8.5 s

    // Pre-compute seed positions from Lissajous equations
    float sx[VORONOI_SEEDS], sy[VORONOI_SEEDS];
    for (uint8_t i = 0; i < VORONOI_SEEDS; i++) {
        sx[i] = voroSeeds[i].cx + voroSeeds[i].rx * sinf(T * voroSeeds[i].fx + voroSeeds[i].px);
        sy[i] = voroSeeds[i].cy + voroSeeds[i].ry * sinf(T * voroSeeds[i].fy + voroSeeds[i].py);
    }

    for (uint8_t y = 0; y < MATRIX_H; y++) {
        for (uint8_t x = 0; x < MATRIX_W; x++) {
            float d1 = 1e9f, d2 = 1e9f;
            uint8_t nearest = 0;
            for (uint8_t i = 0; i < VORONOI_SEEDS; i++) {
                float dx = x - sx[i], dy = y - sy[i];
                float d  = sqrtf(dx*dx + dy*dy);
                if (d < d1) { d2 = d1; d1 = d; nearest = i; }
                else if (d < d2) { d2 = d; }
            }
            // Hue: evenly spaced seeds + global drift
            uint8_t hue = voroBaseHue + nearest * (256 / VORONOI_SEEDS);
            bool isEdge = (d2 - d1) < 1.5f;
            leds[XY(x, y)] = isEdge
                ? CHSV(hue, 60, 255)           // bright, near-white edge
                : CHSV(hue, 220, 200);          // flat-bright cell interior
        }
    }
    FastLED.show();
}

// 9. Boids (flocking) ─────────────────────────────────────────────
// 16 particles obey separation, alignment, and cohesion rules on a
// toroidal grid. Each leaves a fading trail; hue shifts slowly.

#define BOID_SPEED  0.30f
#define BOID_VISION 5.0f

void initBoids() {
    for (uint8_t i = 0; i < NUM_BOIDS; i++) {
        boids[i].x   = random8(MATRIX_W);
        boids[i].y   = random8(MATRIX_H);
        float angle  = random8() * 2.0f * 3.14159f / 256.0f;
        boids[i].vx  = cosf(angle) * BOID_SPEED;
        boids[i].vy  = sinf(angle) * BOID_SPEED;
        boids[i].hue = i * (256 / NUM_BOIDS);
    }
}

void drawBoids(uint32_t now) {
    static uint32_t last = 0;
    if (!frameReady(now, last, 40)) return;

    for (uint8_t i = 0; i < NUM_BOIDS; i++) {
        float sepX = 0, sepY = 0, aliX = 0, aliY = 0, cohX = 0, cohY = 0;
        uint8_t n = 0;
        for (uint8_t j = 0; j < NUM_BOIDS; j++) {
            if (i == j) continue;
            float dx = boids[j].x - boids[i].x;
            float dy = boids[j].y - boids[i].y;
            // Toroidal shortest-path distance
            if (dx >  12.5f) dx -= MATRIX_W;
            if (dx < -12.5f) dx += MATRIX_W;
            if (dy >  12.5f) dy -= MATRIX_H;
            if (dy < -12.5f) dy += MATRIX_H;
            float dist = sqrtf(dx*dx + dy*dy);
            if (dist < BOID_VISION && dist > 0.01f) {
                sepX -= dx / dist;  sepY -= dy / dist;
                aliX += boids[j].vx; aliY += boids[j].vy;
                cohX += boids[j].x;  cohY += boids[j].y;
                n++;
            }
        }
        if (n > 0) {
            cohX = cohX / n - boids[i].x;
            cohY = cohY / n - boids[i].y;
            aliX /= n; aliY /= n;
            boids[i].vx += sepX * 0.05f + aliX * 0.03f + cohX * 0.01f;
            boids[i].vy += sepY * 0.05f + aliY * 0.03f + cohY * 0.01f;
        }
        float spd = sqrtf(boids[i].vx * boids[i].vx + boids[i].vy * boids[i].vy);
        if (spd > BOID_SPEED) {
            boids[i].vx = boids[i].vx / spd * BOID_SPEED;
            boids[i].vy = boids[i].vy / spd * BOID_SPEED;
        }
        if (spd < 0.05f) {
            boids[i].vx += (random8() - 128) * 0.001f;
            boids[i].vy += (random8() - 128) * 0.001f;
        }
        // Wrap position on toroidal grid
        boids[i].x = fmodf(boids[i].x + boids[i].vx + MATRIX_W, (float)MATRIX_W);
        boids[i].y = fmodf(boids[i].y + boids[i].vy + MATRIX_H, (float)MATRIX_H);
    }

    for (uint16_t i = 0; i < NUM_LEDS; i++) leds[i].fadeToBlackBy(60);
    for (uint8_t i = 0; i < NUM_BOIDS; i++)
        leds[XY((uint8_t)boids[i].x, (uint8_t)boids[i].y)] = CHSV(boids[i].hue++, 220, 255);
    FastLED.show();
}

// 10. Mandelbrot zoomer ───────────────────────────────────────────
// Slowly zooms into four classic locations on the Mandelbrot set,
// cycling through them. Float arithmetic is fine down to scale ~5e-4
// on Teensy 3.6's FPU; resets before precision degrades.

#define MAND_MAX_ITER  48
#define MAND_TARGETS   4

static const float mandTgtR[MAND_TARGETS] = {-0.7436f,  0.2750f, -0.7269f, -1.7491f};
static const float mandTgtI[MAND_TARGETS] = { 0.1319f,  0.0000f,  0.1889f,  0.0000f};

static float   mandScale   = 2.5f;
static float   mandCx      = -0.5f;
static float   mandCy      =  0.0f;
static uint8_t mandTarget  = 0;
static uint8_t mandHue     = 0;

void initMandelbrot() {
    mandScale  = 2.5f;
    mandCx     = -0.5f;
    mandCy     =  0.0f;
    mandTarget = 0;
    mandHue    = 0;
}

void drawMandelbrot(uint32_t now) {
    static uint32_t last = 0;
    if (!frameReady(now, last, 33)) return;

    // Pan toward target and zoom in
    mandCx    += (mandTgtR[mandTarget] - mandCx) * 0.005f;
    mandCy    += (mandTgtI[mandTarget] - mandCy) * 0.005f;
    mandScale *= 0.992f;
    mandHue   += 2;

    // Reset: move to next target and zoom out to full set
    if (mandScale < 5e-4f) {
        mandTarget = (mandTarget + 1) % MAND_TARGETS;
        mandScale  = 2.5f;
        mandCx     = -0.5f;
        mandCy     =  0.0f;
    }

    float pixSize = mandScale / MATRIX_W;

    for (uint8_t py = 0; py < MATRIX_H; py++) {
        float ci = mandCy + (py - MATRIX_H * 0.5f) * pixSize;
        for (uint8_t px = 0; px < MATRIX_W; px++) {
            float cr = mandCx + (px - MATRIX_W * 0.5f) * pixSize;
            float zr = 0.0f, zi = 0.0f;
            uint8_t iter = 0;
            while (zr*zr + zi*zi < 4.0f && iter < MAND_MAX_ITER) {
                float tmp = zr*zr - zi*zi + cr;
                zi = 2.0f * zr * zi + ci;
                zr = tmp;
                iter++;
            }
            if (iter == MAND_MAX_ITER) {
                leds[XY(px, py)] = CRGB::Black;
            } else {
                uint8_t hue = (uint8_t)(iter * 5) + mandHue;
                uint8_t bri = map(iter, 0, MAND_MAX_ITER - 1, 60, 255);
                leds[XY(px, py)] = CHSV(hue, 230, bri);
            }
        }
    }
    FastLED.show();
}

// 11. Spectrum Bars ───────────────────────────────────────────────
// 25 columns of rising bars driven by FFT energy, log-spaced from
// ~86 Hz to ~8.6 kHz. Color shifts from green (bass) to blue (treble).
// A white peak-hold dot sits above each bar and falls slowly.

// Returns normalized FFT amplitude for display column col.
// Bin range is exponentially spaced: col 0 ≈ 86 Hz, col 24 ≈ 8.6 kHz.
float fftBand(uint8_t col) {
    // 2 * 100^(col/24) maps col 0→bin 2, col 24→bin 200
    uint16_t lo = (uint16_t)(2.0f * powf(100.0f, (float)col       / (MATRIX_W - 1)));
    uint16_t hi = (uint16_t)(2.0f * powf(100.0f, (float)(col + 1) / (MATRIX_W - 1)));
    if (hi <= lo) hi = lo;  // ensure at least 1 bin wide
    return fft1024.read(lo, hi);
}

void initSpectrum() {
    memset(specPeak,      0, sizeof(specPeak));
    memset(specPeakTimer, 0, sizeof(specPeakTimer));
}

void drawSpectrum(uint32_t now) {
    static uint32_t last = 0;
    if (!frameReady(now, last, 30)) return;

    for (uint8_t col = 0; col < MATRIX_W; col++) {
        float   amp = fftBand(col) * SPEC_SENSITIVITY;
        uint8_t h   = (uint8_t)constrain(amp * MATRIX_H, 0.0f, (float)MATRIX_H);

        // Peak hold: reset on new high, count down, then drop 1 px/frame
        if (h >= specPeak[col]) {
            specPeak[col]      = h;
            specPeakTimer[col] = 18;
        } else if (specPeakTimer[col] > 0) {
            specPeakTimer[col]--;
        } else if (specPeak[col] > 0) {
            specPeak[col]--;
        }

        // Draw bar bottom-up; hue shifts green (96) → teal (156) left to right.
        // Every pixel is written explicitly so no residue from the previous animation.
        uint8_t hue = 96 + map(col, 0, MATRIX_W - 1, 0, 60);
        for (uint8_t row = 0; row < MATRIX_H; row++) {
            uint8_t py = MATRIX_H - 1 - row;
            if (row < h) {
                uint8_t bri = map(row, 0, max((uint8_t)1, h) - 1, 140, 255);
                leds[XY(col, py)] = CHSV(hue, 220, bri);
            } else if (row == specPeak[col] && specPeak[col] > 0) {
                leds[XY(col, py)] = CRGB::White;
            } else {
                leds[XY(col, py)] = CRGB::Black;
            }
        }
    }
    FastLED.show();
}

// 12. Beat Pulse ──────────────────────────────────────────────────
// Monitors bass-band energy. When a beat is detected (energy spikes
// above a running average), a ring expands from the center and fades
// out before the next beat. Hue rotates with each beat.

static uint32_t lastBeat = 0;
static uint8_t  beatHue  = 0;
static float    bassAvg  = 0.0f;

void initBeatPulse() {
    bassAvg  = 0.0f;
    lastBeat = 0;   // elapsed will be huge → bri = 0 → no ring at start
    beatHue  = 0;
}

void drawBeatPulse(uint32_t now) {
    static uint32_t last = 0;
    if (!frameReady(now, last, 25)) return;

    // Bass energy: bins 2–8 ≈ 86–344 Hz
    float bass = fft1024.read(2, 8) * 5.0f;
    bassAvg = bassAvg * 0.94f + bass * 0.06f;

    if (bass > bassAvg * BEAT_THRESHOLD && bass > BEAT_MIN_LEVEL) {
        lastBeat = now;
        beatHue += 42;
    }

    // Fade the background; trails persist briefly between rings
    for (uint16_t i = 0; i < NUM_LEDS; i++) leds[i].fadeToBlackBy(25);

    // Expanding ring
    float   elapsed = (now - lastBeat) / 1000.0f;
    float   radius  = elapsed * 18.0f;
    uint8_t bri     = (uint8_t)constrain(255.0f - elapsed * 280.0f, 0.0f, 255.0f);

    if (bri > 0) {
        for (uint8_t y = 0; y < MATRIX_H; y++) {
            for (uint8_t x = 0; x < MATRIX_W; x++) {
                float dx   = (float)x - 12.0f;
                float dy   = (float)y - 12.0f;
                float d    = sqrtf(dx*dx + dy*dy);
                float diff = fabsf(d - radius);
                if (diff < 1.8f) {
                    uint8_t b = (uint8_t)((1.8f - diff) / 1.8f * bri);
                    leds[XY(x, y)] = CHSV(beatHue, 240, b);
                }
            }
        }
    }
    FastLED.show();
}

// ── Animation registry ────────────────────────────────────────────

struct Anim {
    const char* name;
    void (*init)();
    void (*draw)(uint32_t now);
    uint16_t frameMs;  // base frame interval at speedScale=128
};

const Anim anims[] = {
    { "plasma",    nullptr,              drawPlasma,           18  },
    { "ripple",    nullptr,              drawRipple,           20  },
    { "twinkle",   nullptr,              drawTwinkle,          35  },
    { "fire",      initFire,             drawFire,             25  },
    { "waves",     initWaveInterference, drawWaveInterference, 22  },
    { "reaction",  initRD,               drawRD,               33  },
    { "life",      initGoL,              drawGoL,              140 },
    { "voronoi",   initVoronoi,          drawVoronoi,          33  },
    { "boids",     initBoids,            drawBoids,            40  },
    { "mandelbrot", initMandelbrot,       drawMandelbrot,       33  },
    { "spectrum",  initSpectrum,         drawSpectrum,         30  },
    { "beatpulse", initBeatPulse,        drawBeatPulse,        25  },
};
const uint8_t NUM_ANIMS = sizeof(anims) / sizeof(anims[0]);

uint8_t       animIdx   = 0;
unsigned long animStart = 0;

#define ANIM_DURATION_MS 30000UL

// ── Input events ──────────────────────────────────────────────────
// Hardware events arrive via pollEncoder() (encoder + button) or
// handleSerial() (USB). Audio events will hook in here in Phase 4.

void selectAnim(uint8_t idx) {
    animIdx   = idx % NUM_ANIMS;
    animStart = millis();
    FastLED.clear();
    FastLED.show();
    if (anims[animIdx].init) anims[animIdx].init();
}

void handleEvent(InputEvent e) {
    switch (e) {
        case NEXT_ANIM:    selectAnim(animIdx + 1); break;
        case PREV_ANIM:    selectAnim((animIdx + NUM_ANIMS - 1) % NUM_ANIMS); break;
        case BRIGHT_UP:    brightness = qadd8(brightness, 16); FastLED.setBrightness(brightness); break;
        case BRIGHT_DOWN:  brightness = qsub8(brightness, 16); FastLED.setBrightness(brightness); break;
        case SPEED_UP:     speedScale = qadd8(speedScale, 32); break;
        case SPEED_DOWN:   speedScale = max((uint8_t)16, qsub8(speedScale, 32)); break;
        case TOGGLE_CYCLE: autoCycle  = !autoCycle; break;
    }
}

// ── USB serial command handler ─────────────────────────────────────
// 9600 baud, newline-terminated. Commands: n p b+ b- s+ s- c ?
// Connect via Arduino Serial Monitor or any terminal.

void handleSerial() {
    // ── Binary frame accumulation ─────────────────────────────────
    // If a frame receive is in progress, drain available bytes first.
    if (frameRecv) {
        while (Serial.available() && framePos < GRID_LEDS * 3)
            frameBuf[framePos++] = (uint8_t)Serial.read();
        if (framePos >= GRID_LEDS * 3) {
            for (uint16_t i = 0; i < GRID_LEDS; i++)
                leds[XY(i % MATRIX_W, i / MATRIX_W)] =
                    CRGB(frameBuf[i*3], frameBuf[i*3+1], frameBuf[i*3+2]);
            FastLED.show();
            frameRecv = false;
            framePos  = 0;
        }
        return;
    }

    // ── Text command accumulation ─────────────────────────────────
    static String buf;
    while (Serial.available()) {
        char c = (char)Serial.read();

        // 'F' as the very first byte of a new command starts a binary frame.
        if (c == 'F' && buf.length() == 0) {
            drawMode  = true;
            frameRecv = true;
            framePos  = 0;
            return;
        }

        if (c == '\n' || c == '\r') {
            buf.trim();
            if (buf.length() == 0) { buf = ""; continue; }

            // ── Legacy one-character commands ─────────────────────
            if      (buf == "n")  handleEvent(NEXT_ANIM);
            else if (buf == "p")  handleEvent(PREV_ANIM);
            else if (buf == "b+") handleEvent(BRIGHT_UP);
            else if (buf == "b-") handleEvent(BRIGHT_DOWN);
            else if (buf == "s+") handleEvent(SPEED_UP);
            else if (buf == "s-") handleEvent(SPEED_DOWN);
            else if (buf == "c")  handleEvent(TOGGLE_CYCLE);

            // ── Draw mode ─────────────────────────────────────────
            else if (buf == "d")  { drawMode = true;  FastLED.clear(); FastLED.show(); }
            else if (buf == "D")  { drawMode = false; selectAnim(animIdx); }
            else if (buf == "ps") { FastLED.show(); }
            else if (buf == "pC") { FastLED.clear(); FastLED.show(); }
            else if (buf.startsWith("px")) {
                uint8_t x, y, r, g, b;
                if (sscanf(buf.c_str(), "px%hhu,%hhu,%hhu,%hhu,%hhu", &x, &y, &r, &g, &b) == 5)
                    leds[XY(x, y)] = CRGB(r, g, b);
            }

            // ── Direct-value commands ─────────────────────────────
            else if (buf[0] == 'a' && buf.length() > 1) {
                selectAnim((uint8_t)buf.substring(1).toInt());
            }
            else if (buf[0] == 'B' && buf.length() > 1) {
                brightness = (uint8_t)constrain(buf.substring(1).toInt(), 0, 255);
                FastLED.setBrightness(brightness);
            }
            else if (buf[0] == 'S' && buf.length() > 1) {
                speedScale = (uint8_t)constrain(buf.substring(1).toInt(), 1, 255);
            }

            // ── Status — JSON for easy Python parsing ─────────────
            else if (buf == "?") {
                Serial.print(F("{\"anim\":"));     Serial.print(animIdx);
                Serial.print(F(",\"name\":\""));   Serial.print(anims[animIdx].name);
                Serial.print(F("\",\"bright\":")); Serial.print(brightness);
                Serial.print(F(",\"speed\":"));    Serial.print(speedScale);
                Serial.print(F(",\"cycle\":"));    Serial.print(autoCycle ? "true" : "false");
                Serial.print(F(",\"draw\":"));     Serial.print(drawMode  ? "true" : "false");
                Serial.print(F(",\"anims\":["));
                for (uint8_t i = 0; i < NUM_ANIMS; i++) {
                    Serial.print('"'); Serial.print(anims[i].name); Serial.print('"');
                    if (i < NUM_ANIMS - 1) Serial.print(',');
                }
                Serial.println(F("]}"));
            }

            buf = "";
        } else {
            buf += c;
        }
    }
}

// ── Encoder / button polling ──────────────────────────────────────
// MODE_ANIM  → rotate: next/prev animation;  click: enter brightness mode
// MODE_BRIGHT → rotate: brightness ±16;       click: return to anim mode
//
// Encoder counts: the Encoder library accumulates signed ticks. A full
// mechanical detent on a typical EC11 produces ENC_COUNTS_PER_STEP ticks,
// so we fire one event per detent rather than one per raw edge.
//
// Button debounce: we track the raw pin level and only act once it has
// been stable for 20 ms (avoids contact bounce on cheap switches).
void pollEncoder() {
    // ── Rotation ──────────────────────────────────────────────────
    static long lastPos = 0;
    long pos   = enc.read();
    long steps = (pos - lastPos) / ENC_COUNTS_PER_STEP;
    if (steps != 0) {
        lastPos += steps * ENC_COUNTS_PER_STEP;
        if (encMode == MODE_ANIM) {
            if (steps > 0) handleEvent(NEXT_ANIM); else handleEvent(PREV_ANIM);
        } else {
            if (steps > 0) handleEvent(BRIGHT_UP); else handleEvent(BRIGHT_DOWN);
        }
    }

    // ── Button ────────────────────────────────────────────────────
    static bool     lastRaw    = HIGH;
    static bool     lastStable = HIGH;
    static uint32_t debounceAt = 0;
    bool     raw = digitalRead(PIN_ENC_BTN);
    uint32_t now = millis();
    if (raw != lastRaw) { debounceAt = now; lastRaw = raw; }
    if (now - debounceAt > 20 && raw != lastStable) {
        lastStable = raw;
        if (lastStable == LOW)   // falling edge = confirmed press
            encMode = (encMode == MODE_ANIM) ? MODE_BRIGHT : MODE_ANIM;
    }
}

// ── Setup & main loop ─────────────────────────────────────────────

void setup() {
    Serial.begin(115200);
    AudioMemory(12);
    pinMode(PIN_ENC_BTN, INPUT_PULLUP);
    FastLED.addLeds<OCTOWS2811>(leds, LEDS_PER_STRIP);
    FastLED.setBrightness(brightness);
    FastLED.clear();
    FastLED.show();
    delay(500);
    animStart = millis();
    if (anims[animIdx].init) anims[animIdx].init();
}

void loop() {
    uint32_t now = millis();
    if (!drawMode) {
        if (autoCycle && now - animStart >= ANIM_DURATION_MS)
            handleEvent(NEXT_ANIM);
        anims[animIdx].draw(now);
    }
    pollEncoder();
    handleSerial();
}
