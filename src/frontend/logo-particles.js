"use strict";

// The animated logo on the landing page (v1.1 spec 4.12): the mark is made
// of nodes. It holds, breaks apart into particles that swirl out and settle
// into the word "Collectivize", holds (particles near the pointer or finger
// get pushed away and spring back), then contracts back into the mark, on
// a loop. Each particle is a glyph (@ # * + = - .), heavier where the
// source image is denser, coloured from the logo.
//
// Both shapes are sampled from the real brand SVGs (public/brand), drawn
// to an offscreen canvas. One canvas, driven by requestAnimationFrame;
// paused when the tab is hidden or the canvas is off screen. With
// prefers-reduced-motion, or if anything fails, the static logo stays.
//
// Usage: LogoParticles.mount(container, { mark, word }) -> boolean

(() => {
    const GLYPHS = [".", "-", "=", "+", "*", "#", "@"]; // light to heavy
    const HOLD_MARK = 1500; // ms (placeholders, per the spec)
    const UNRAVEL = 1400;
    const HOLD_WORD = 3000;
    const CONTRACT = 1400;
    const CYCLE = HOLD_MARK + UNRAVEL + HOLD_WORD + CONTRACT;
    const PUSH_RADIUS = 70;
    const PUSH_STRENGTH = 26;
    const SPRING = 0.12;
    const DAMPING = 0.78;

    const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

    function loadImage(src) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            img.onload = () => resolve(img);
            img.onerror = reject;
            img.src = src;
        });
    }

    /** Points on a grid where the image has ink, with colour and density.
     *  `thicken` (px) widens thin strokes first, so a line drawing like the
     *  wordmark doesn't fall between the grid's points. */
    function sample(img, width, height, step, thicken = 0) {
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext("2d", { willReadFrequently: true });
        const pad = Math.ceil(thicken);
        for (let dy = -pad; dy <= pad; dy += 1) {
            for (let dx = -pad; dx <= pad; dx += 1) {
                if (dx * dx + dy * dy <= thicken * thicken) ctx.drawImage(img, dx, dy, width, height);
            }
        }
        const { data } = ctx.getImageData(0, 0, width, height);
        const points = [];
        for (let y = 0; y < height; y += step) {
            for (let x = 0; x < width; x += step) {
                // Density: how much of the cell is ink.
                let ink = 0;
                let r = 0;
                let g = 0;
                let b = 0;
                let n = 0;
                for (let dy = 0; dy < step; dy += 2) {
                    for (let dx = 0; dx < step; dx += 2) {
                        const i = (((y + dy) * width) + (x + dx)) * 4;
                        if (i >= data.length) continue;
                        // Paper-coloured fills (the wordmark has some) aren't ink.
                        const paper = data[i] > 225 && data[i + 1] > 225 && data[i + 2] > 220;
                        const alpha = paper ? 0 : data[i + 3] / 255;
                        ink += alpha;
                        n += 1;
                        if (alpha > 0.3) {
                            r += data[i];
                            g += data[i + 1];
                            b += data[i + 2];
                        }
                    }
                }
                const density = ink / Math.max(1, n);
                if (density < 0.12) continue;
                const solid = Math.max(1, Math.round(density * n));
                points.push({
                    x: x + step / 2,
                    y: y + step / 2,
                    density,
                    color: `rgb(${Math.round(r / solid)}, ${Math.round(g / solid)}, ${Math.round(b / solid)})`,
                });
            }
        }
        return points;
    }

    class LogoParticles {
        static async mount(container, { mark, word }) {
            if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return false;
            try {
                const [markImg, wordImg] = await Promise.all([loadImage(mark), loadImage(word)]);
                new LogoParticles(container, markImg, wordImg);
                return true;
            } catch {
                return false; // the static logo stays
            }
        }

        constructor(container, markImg, wordImg) {
            this.canvas = document.createElement("canvas");
            this.canvas.className = "logo-particles";
            this.canvas.setAttribute("role", "img");
            this.canvas.setAttribute("aria-label", "Collectivize");
            container.replaceChildren(this.canvas);
            this.ctx = this.canvas.getContext("2d");
            this.markImg = markImg;
            this.wordImg = wordImg;
            this.pointer = null;
            this.running = false;
            this.visible = true;
            this.start = performance.now();

            this.resize();
            new ResizeObserver(() => this.resize()).observe(this.canvas);
            this.bindPointer();
            document.addEventListener("visibilitychange", () => this.updateRunning());
            new IntersectionObserver(([entry]) => {
                this.visible = entry.isIntersecting;
                this.updateRunning();
            }).observe(this.canvas);
            this.updateRunning();
        }

        resize() {
            const rect = this.canvas.getBoundingClientRect();
            const dpr = Math.min(2, window.devicePixelRatio || 1);
            const w = Math.max(200, Math.round(rect.width));
            const h = Math.max(160, Math.round(rect.height));
            if (this.w === w && this.h === h) return;
            this.w = w;
            this.h = h;
            this.dpr = dpr;
            this.canvas.width = Math.round(w * dpr);
            this.canvas.height = Math.round(h * dpr);
            this.build();
        }

        /** Sample both shapes to fit the canvas, and pair them up. */
        build() {
            const { w, h } = this;
            const step = Math.max(6, Math.round(Math.min(w, h) / 46));
            this.markFont = step * 1.35;
            const markSize = Math.min(w, h) * 0.86;
            const markPts = sample(this.markImg, Math.round(markSize), Math.round(markSize * 482 / 490), step);
            const wordW = Math.round(w * 0.96);
            const wordH = Math.round(wordW * 106 / 1004);
            const wordStep = Math.max(4, Math.round(step * 0.6));
            this.wordRect = { x: (w - wordW) / 2, y: (h - wordH) / 2, w: wordW, h: wordH };
            // On small screens the word is too short to read as glyphs alone,
            // so the crisp wordmark fades in beneath them while it holds.
            this.wordUnderlay = w < 520 ? 0.85 : 0.18;
            this.wordFont = wordStep * 1.3;
            const wordPts = sample(this.wordImg, wordW, wordH, wordStep, wordStep * 0.45);
            const offset = (pts, width, height) => pts.map((p) => ({ ...p, x: p.x + (w - width) / 2, y: p.y + (h - height) / 2 }));
            const a = offset(markPts, markSize, markSize * 482 / 490);
            const b = offset(wordPts, wordW, wordH);
            // One pool, sized to the larger shape; extras fade out in the smaller one.
            const count = Math.max(a.length, b.length);
            const pick = (pts, i) => (pts.length ? pts[i % pts.length] : { x: w / 2, y: h / 2, density: 0, color: "#202b29" });
            // Pair by angle around the centre, so the unravel swirls instead of crossing.
            const byAngle = (pts) => [...pts].sort((p, q) => Math.atan2(p.y - h / 2, p.x - w / 2) - Math.atan2(q.y - h / 2, q.x - w / 2));
            const sa = byAngle(a);
            const sb = byAngle(b);
            this.particles = Array.from({ length: count }, (_, i) => {
                const from = pick(sa, Math.floor(i * sa.length / count));
                const to = pick(sb, Math.floor(i * sb.length / count));
                return {
                    a: from, b: to,
                    inA: i < sa.length || sa.length >= count, inB: i < sb.length || sb.length >= count,
                    swirl: (Math.random() - 0.5) * 2,
                    x: from.x, y: from.y, vx: 0, vy: 0, ox: 0, oy: 0,
                };
            });
        }

        bindPointer() {
            const move = (event) => {
                const rect = this.canvas.getBoundingClientRect();
                this.pointer = { x: event.clientX - rect.left, y: event.clientY - rect.top };
            };
            this.canvas.addEventListener("pointermove", move);
            this.canvas.addEventListener("pointerdown", move);
            this.canvas.addEventListener("pointerleave", () => { this.pointer = null; });
            this.canvas.addEventListener("pointerup", (event) => {
                if (event.pointerType !== "mouse") this.pointer = null;
            });
        }

        updateRunning() {
            const shouldRun = this.visible && !document.hidden;
            if (shouldRun && !this.running) {
                this.running = true;
                this.last = performance.now();
                requestAnimationFrame((t) => this.frame(t));
            } else if (!shouldRun && this.running) {
                this.running = false;
                this.pausedAt = performance.now();
            }
            if (shouldRun && this.pausedAt) {
                this.start += performance.now() - this.pausedAt; // resume where it left off
                this.pausedAt = 0;
            }
        }

        frame(now) {
            if (!this.running) return;
            const t = (now - this.start) % CYCLE;
            this.draw(t);
            requestAnimationFrame((next) => this.frame(next));
        }

        /** Where we are in the loop: 0 = the mark, 1 = the word. */
        phase(t) {
            if (t < HOLD_MARK) return { mix: 0, swirl: 0, interactive: false };
            if (t < HOLD_MARK + UNRAVEL) {
                const k = (t - HOLD_MARK) / UNRAVEL;
                return { mix: ease(k), swirl: Math.sin(Math.PI * k), interactive: false };
            }
            if (t < HOLD_MARK + UNRAVEL + HOLD_WORD) return { mix: 1, swirl: 0, interactive: true };
            const k = (t - HOLD_MARK - UNRAVEL - HOLD_WORD) / CONTRACT;
            return { mix: 1 - ease(k), swirl: -Math.sin(Math.PI * k), interactive: false };
        }

        draw(t) {
            const { ctx, w, h, dpr } = this;
            const { mix, swirl, interactive } = this.phase(t);
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            ctx.clearRect(0, 0, w, h);
            const size = this.markFont + (this.wordFont - this.markFont) * mix;
            ctx.font = `700 ${size.toFixed(1)}px ui-monospace, "SF Mono", Menlo, monospace`;
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            const cx = w / 2;
            const cy = h / 2;
            if (mix > 0.6) {
                const r = this.wordRect;
                ctx.globalAlpha = ((mix - 0.6) / 0.4) * this.wordUnderlay;
                ctx.drawImage(this.wordImg, r.x, r.y, r.w, r.h);
                ctx.globalAlpha = 1;
            }
            for (const p of this.particles) {
                // Home: between the two shapes, swirled outward mid-flight.
                let hx = p.a.x + (p.b.x - p.a.x) * mix;
                let hy = p.a.y + (p.b.y - p.a.y) * mix;
                if (swirl) {
                    const dx = hx - cx;
                    const dy = hy - cy;
                    const angle = swirl * (0.9 + 0.5 * p.swirl);
                    const grow = 1 + 0.35 * Math.abs(swirl);
                    hx = cx + (dx * Math.cos(angle) - dy * Math.sin(angle)) * grow;
                    hy = cy + (dx * Math.sin(angle) + dy * Math.cos(angle)) * grow;
                }
                // The word reacts to the pointer: pushed away, then springs back.
                if (interactive && this.pointer) {
                    const dx = p.x - this.pointer.x;
                    const dy = p.y - this.pointer.y;
                    const dist = Math.hypot(dx, dy);
                    if (dist < PUSH_RADIUS && dist > 0.01) {
                        const force = (1 - dist / PUSH_RADIUS) * PUSH_STRENGTH;
                        p.vx += (dx / dist) * force * 0.2;
                        p.vy += (dy / dist) * force * 0.2;
                    }
                }
                // A damped spring pulls each particle back to its home.
                p.vx = (p.vx - p.ox * SPRING) * DAMPING;
                p.vy = (p.vy - p.oy * SPRING) * DAMPING;
                p.ox += p.vx;
                p.oy += p.vy;
                p.x = hx + p.ox;
                p.y = hy + p.oy;

                const alpha = (p.inA ? 1 - mix : 0) + (p.inB ? mix : 0);
                if (alpha < 0.03) continue;
                const density = p.a.density + (p.b.density - p.a.density) * mix;
                const glyph = GLYPHS[Math.min(GLYPHS.length - 1, Math.floor(density * GLYPHS.length))];
                ctx.globalAlpha = Math.min(1, alpha);
                ctx.fillStyle = mix < 0.5 ? p.a.color : p.b.color;
                ctx.fillText(glyph, p.x, p.y);
            }
            ctx.globalAlpha = 1;
        }
    }

    window.LogoParticles = LogoParticles;
})();
