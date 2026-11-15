import { useEffect, useRef } from "react";

const nodes = Array.from({ length: 78 }, (_, i) => {
  const angle = i * 2.399963;
  const y = 1 - (i / 77) * 2;
  const radius = Math.sqrt(1 - y * y) * (0.45 + (i % 5) * 0.13);
  return { x: Math.cos(angle) * radius, y: y * 0.85, z: Math.sin(angle) * radius, hub: i % 13 === 0 };
});
const linkKeys = new Set<string>();
const links: [number, number][] = [];
nodes.forEach((node, i) => {
  nodes.map((other, j) => ({ j, distance: (node.x - other.x) ** 2 + (node.y - other.y) ** 2 + (node.z - other.z) ** 2 }))
    .filter(({ j }) => j !== i).sort((a, b) => a.distance - b.distance).slice(0, 3)
    .forEach(({ j }) => {
      const key = [Math.min(i, j), Math.max(i, j)].join(":");
      if (!linkKeys.has(key)) { linkKeys.add(key); links.push([i, j]); }
    });
});

export function NetworkBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0, width = 0, height = 0;

    function draw(time: number) {
      if (!ctx) return;
      ctx.clearRect(0, 0, width, height);
      const angle = time * 0.000055;
      const sin = Math.sin(angle), cos = Math.cos(angle);
      const scale = Math.min(Math.max(width, height) * 0.43, 650);
      const points = nodes.map((node) => {
        const x = node.x * cos + node.z * sin;
        const z = -node.x * sin + node.z * cos;
        const y = node.y * 0.92 - z * 0.39;
        const depth = node.y * 0.39 + z * 0.92;
        const perspective = 3 / (3 - depth);
        return { x: width / 2 + x * scale * perspective, y: height / 2 + y * scale * perspective, depth, perspective, hub: node.hub };
      });
      ctx.lineWidth = 1;
      for (const [a, b] of links) {
        ctx.globalAlpha = 0.12 + (points[a].depth + points[b].depth + 2) * 0.07;
        ctx.beginPath(); ctx.moveTo(points[a].x, points[a].y); ctx.lineTo(points[b].x, points[b].y); ctx.stroke();
      }
      for (const point of points) {
        const radius = (point.hub ? 3 : 1.6) * point.perspective;
        ctx.globalAlpha = 0.4 + (point.depth + 1) * 0.22;
        ctx.beginPath(); ctx.arc(point.x, point.y, radius, 0, Math.PI * 2); ctx.fill();
        if (point.hub) {
          ctx.globalAlpha *= 0.5;
          ctx.beginPath(); ctx.arc(point.x, point.y, radius * 2.2, 0, Math.PI * 2); ctx.stroke();
        }
      }
      ctx.globalAlpha = 1;
    }
    function resize() {
      if (!canvas || !ctx) return;
      width = canvas.clientWidth; height = canvas.clientHeight;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = width * dpr; canvas.height = height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.strokeStyle = ctx.fillStyle = getComputedStyle(canvas).color;
      draw(0);
    }
    function tick(time: number) { draw(time); frame = requestAnimationFrame(tick); }
    function updateMotion() {
      cancelAnimationFrame(frame);
      if (motion.matches) draw(0);
      else frame = requestAnimationFrame(tick);
    }
    resize(); updateMotion();
    window.addEventListener("resize", resize);
    motion.addEventListener("change", updateMotion);
    const observer = new MutationObserver(resize);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", resize);
      motion.removeEventListener("change", updateMotion);
      observer.disconnect();
    };
  }, []);

  return <canvas ref={canvasRef} className="xi-auth-network" aria-hidden="true" />;
}
