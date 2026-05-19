// wall_v2 — 25×25 LED grid hardware test
// Teensy 3.x/4.x + OctoWS2811 adapter + WS2812B LEDs

#define USE_OCTOWS2811
#include <OctoWS2811.h>
#include <FastLED.h>

// ── Grid ─────────────────────────────────────────────────────────
#define MATRIX_W    25
#define MATRIX_H    25
#define GRID_LEDS   (MATRIX_W * MATRIX_H)   // 625

#define LEDS_PER_STRIP  100
#define NUM_STRIPS      8
#define NUM_LEDS        (LEDS_PER_STRIP * NUM_STRIPS)  // 1000

CRGB leds[NUM_LEDS];

// Map (x, y) grid coordinates to leds[] index.
//
// Physical layout (confirmed by strip isolation test):
//   Strips 0–6: 3 rows each (75 LEDs), occupying slots 0–74 of each 100-slot block.
//   Strip  7:   4 rows       (100 LEDs), rows 21–24.
//
// Serpentine direction per row (matches Scala WallSerial2p5.pixelIndex):
//   y%3 == 1          → right-to-left
//   y == 24 (last)    → right-to-left
//   all other rows    → left-to-right
uint16_t XY(uint8_t x, uint8_t y) {
    if (x >= MATRIX_W || y >= MATRIX_H) return 0;

    uint8_t strip    = (y < 21) ? y / 3 : 7;
    uint8_t stripRow = (y < 21) ? y % 3 : y - 21;
    bool    reversed = (y % 3 == 1) || (y == MATRIX_H - 1);
    uint8_t col      = reversed ? (MATRIX_W - 1 - x) : x;

    return (uint16_t)strip * LEDS_PER_STRIP + stripRow * MATRIX_W + col;
}


// ── Setup ─────────────────────────────────────────────────────────
void setup() {
    FastLED.addLeds<OCTOWS2811>(leds, LEDS_PER_STRIP);
    FastLED.setBrightness(128);  // ~25% — safe for bench testing without a full PSU
    FastLED.clear();
    FastLED.show();
    delay(500);
}

// ── Main loop ─────────────────────────────────────────────────────
void loop() {
    testStripIsolation(); // light each OctoWS2811 strip a distinct color to map physical layout
    testRawOrder();       // light all 800 LEDs in raw index order, ignoring XY mapping
    testSolidColors();    // confirm all LEDs fire and color channels are correct
    testRowScan();        // verify row order and serpentine direction
    testColScan();        // verify XY column mapping
    testDiagonalRainbow();
    testPixelFill();      // light pixels one-by-one to expose miswired segments
}

// ── Patterns ──────────────────────────────────────────────────────

// Light each OctoWS2811 strip (100 LEDs) a distinct color, one strip at a time.
// Lets you identify which physical region of the display belongs to each strip output.
// Strip colors: 0=Red 1=Orange 2=Yellow 3=Green 4=Cyan 5=Blue 6=Magenta 7=White
void testStripIsolation() {
    const CRGB stripColors[8] = {
        CRGB::Red, CRGB(255,80,0), CRGB::Yellow, CRGB::Green,
        CRGB::Cyan, CRGB::Blue, CRGB::Magenta, CRGB::White
    };
    for (uint8_t s = 0; s < NUM_STRIPS; s++) {
        FastLED.clear();
        uint16_t base = s * LEDS_PER_STRIP;
        for (uint16_t i = base; i < base + LEDS_PER_STRIP; i++)
            leds[i] = stripColors[s];
        FastLED.show();
        delay(1200);
    }
    FastLED.clear();
    FastLED.show();
    delay(300);
}

// Light all NUM_LEDS (800) in raw index order, one at a time.
// Ignores XY mapping — reveals the physical wiring sequence across all 8 strips.
void testRawOrder() {
    FastLED.clear();
    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        leds[i] = CHSV(map(i, 0, NUM_LEDS - 1, 0, 224), 255, 200);
        FastLED.show();
        delay(10);
    }
    delay(800);
    FastLED.clear();
    FastLED.show();
    delay(300);
}

void testSolidColors() {
    const CRGB colors[] = { CRGB::Red, CRGB::Green, CRGB::Blue, CRGB::White };
    for (CRGB c : colors) {
        fill_solid(leds, NUM_LEDS, c);
        FastLED.show();
        delay(800);
    }
    FastLED.clear();
    FastLED.show();
    delay(300);
}

void testRowScan() {
    for (uint8_t y = 0; y < MATRIX_H; y++) {
        FastLED.clear();
        for (uint8_t x = 0; x < MATRIX_W; x++)
            leds[XY(x, y)] = CRGB::Cyan;
        FastLED.show();
        delay(80);
    }
    FastLED.clear();
    FastLED.show();
    delay(300);
}

void testColScan() {
    for (uint8_t x = 0; x < MATRIX_W; x++) {
        FastLED.clear();
        for (uint8_t y = 0; y < MATRIX_H; y++)
            leds[XY(x, y)] = CRGB::Magenta;
        FastLED.show();
        delay(80);
    }
    FastLED.clear();
    FastLED.show();
    delay(300);
}

void testDiagonalRainbow() {
    for (uint16_t t = 0; t < 512; t++) {
        for (uint8_t y = 0; y < MATRIX_H; y++)
            for (uint8_t x = 0; x < MATRIX_W; x++)
                leds[XY(x, y)] = CHSV(((x + y) * 8 + t) & 0xFF, 240, 200);
        FastLED.show();
        delay(16);  // ~60 fps
    }
    FastLED.clear();
    FastLED.show();
    delay(300);
}

void testPixelFill() {
    FastLED.clear();
    for (uint8_t y = 0; y < MATRIX_H; y++) {
        for (uint8_t x = 0; x < MATRIX_W; x++) {
            uint16_t i = y * MATRIX_W + x;
            leds[XY(x, y)] = CHSV(map(i, 0, GRID_LEDS - 1, 0, 224), 255, 200);
            FastLED.show();
            delay(15);
        }
    }
    delay(600);
    FastLED.clear();
    FastLED.show();
    delay(300);
}
