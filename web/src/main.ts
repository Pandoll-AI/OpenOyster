import "./style.css";

/* ---- scroll reveals (motion #2) ---- */
const io = new IntersectionObserver(
  (entries) => {
    for (const e of entries) {
      if (e.isIntersecting) {
        e.target.classList.add("in");
        io.unobserve(e.target);
      }
    }
  },
  { threshold: 0.14, rootMargin: "0px 0px -8% 0px" }
);
document.querySelectorAll(".reveal").forEach((el, i) => {
  (el as HTMLElement).style.transitionDelay = `${Math.min(i % 4, 3) * 70}ms`;
  io.observe(el);
});

/* ---- gentle parallax on the galaxy veil as you scroll (motion #3) ---- */
const galaxy = document.getElementById("galaxy");
let ticking = false;
addEventListener(
  "scroll",
  () => {
    if (ticking || !galaxy) return;
    ticking = true;
    requestAnimationFrame(() => {
      const y = Math.min(scrollY, innerHeight * 2);
      galaxy.style.transform = `translate3d(0, ${(y * 0.06).toFixed(1)}px, 0)`;
      galaxy.style.opacity = String(Math.max(0.35, 1 - y / (innerHeight * 1.6)));
      ticking = false;
    });
  },
  { passive: true }
);

/* ---- intro video popup ---- */
const intro = document.getElementById("intro") as HTMLElement | null;
const video = document.getElementById("introVideo") as HTMLVideoElement | null;
const btnClose = document.getElementById("introClose");
const btnSound = document.getElementById("introSound");

function closeIntro() {
  if (!intro) return;
  intro.style.animation = "fade 0.35s ease reverse";
  setTimeout(() => {
    intro.hidden = true;
    video?.pause();
  }, 300);
}

async function openIntro() {
  if (!intro || !video) return;
  // Only show when the video asset actually exists (graceful skip otherwise).
  try {
    const head = await fetch("/intro.mp4", { method: "HEAD" });
    if (!head.ok) return;
  } catch {
    return;
  }
  intro.hidden = false;
  video.muted = true;
  try {
    await video.play();
  } catch {
    /* autoplay blocked — the poster + controls remain */
  }
  video.addEventListener("ended", closeIntro, { once: true });
}

btnClose?.addEventListener("click", closeIntro);
btnSound?.addEventListener("click", () => {
  if (!video) return;
  video.muted = !video.muted;
  if (!video.muted) video.play().catch(() => {});
  (btnSound as HTMLButtonElement).textContent = video.muted ? "🔇 소리 켜기" : "🔊 소리 끄기";
});
addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeIntro();
});

openIntro();
