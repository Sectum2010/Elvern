import { screenshots } from "./screenshots.manifest.js";

document.documentElement.classList.add("js");

const screenshotPath = (file) => `./assets/screenshots/${encodeURIComponent(file)}`;
const reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
const reducedMotion = reducedMotionQuery.matches;

function findShot(section, caption) {
  return screenshots.find((shot) => (
    shot.section === section
    && (!caption || shot.caption === caption)
  ));
}

function createImage(shot, className = "") {
  const image = document.createElement("img");
  image.src = screenshotPath(shot.file);
  image.alt = shot.alt;
  image.loading = "lazy";
  image.decoding = "async";
  if (className) {
    image.className = className;
  }
  return image;
}

function mountShotSlots() {
  document.querySelectorAll("[data-shot]").forEach((slot) => {
    const section = slot.dataset.shot;
    const caption = slot.dataset.caption;
    const shot = findShot(section, caption);
    if (!shot) {
      slot.setAttribute("aria-label", "Screenshot placeholder");
      return;
    }
    slot.replaceChildren(createImage(shot));
  });
}

function mountGallery(section, limit = Infinity) {
  const gallery = document.querySelector(`[data-gallery="${section}"]`);
  if (!gallery) {
    return;
  }
  const shots = screenshots
    .filter((shot) => shot.section === section)
    .slice(0, limit);
  gallery.replaceChildren(
    ...shots.map((shot) => {
      const figure = document.createElement("figure");
      figure.className = "reveal";
      const slot = document.createElement("div");
      slot.className = "shot-slot";
      slot.append(createImage(shot, "parallax-shot"));
      const caption = document.createElement("figcaption");
      caption.textContent = shot.caption;
      figure.append(slot, caption);
      return figure;
    }),
  );
}

function setLiteStep(index) {
  const safeIndex = Math.max(0, Math.min(3, Number(index) || 0));
  document.querySelectorAll("[data-story-step]").forEach((step) => {
    step.classList.toggle("is-active", Number(step.dataset.storyStep) === safeIndex);
  });
  document.querySelectorAll("[data-lite-frame]").forEach((frame, frameIndex) => {
    frame.classList.toggle("is-active", frameIndex === safeIndex);
  });
}

function setCountdownFromProgress(progress) {
  const countdown = document.querySelector("[data-countdown]");
  if (!countdown) {
    return;
  }
  const stepStart = 0.25;
  const stepEnd = 0.5;
  const localProgress = Math.max(0, Math.min(1, (progress - stepStart) / (stepEnd - stepStart)));
  const remaining = Math.max(0, 17 - Math.round(localProgress * 17));
  countdown.textContent = `EST 0:${String(remaining).padStart(2, "0")}`;
}

function initHeader() {
  const header = document.querySelector("[data-header]");
  if (!header) {
    return;
  }
  const updateHeader = () => {
    header.classList.toggle("is-scrolled", window.scrollY > 16);
  };
  updateHeader();
  window.addEventListener("scroll", updateHeader, { passive: true });
}

function initTilt() {
  const card = document.querySelector("[data-tilt-card]");
  const finePointer = window.matchMedia("(pointer: fine)").matches;
  if (!card || reducedMotion || !finePointer) {
    return;
  }
  card.addEventListener("pointermove", (event) => {
    const rect = card.getBoundingClientRect();
    const x = (event.clientX - rect.left) / rect.width - 0.5;
    const y = (event.clientY - rect.top) / rect.height - 0.5;
    card.style.transform = `perspective(1100px) rotateX(${(-y * 4).toFixed(2)}deg) rotateY(${(x * 5).toFixed(2)}deg) translateY(-4px)`;
  });
  card.addEventListener("pointerleave", () => {
    card.style.transform = "";
  });
}

function initSmoothScroll() {
  if (reducedMotion || !window.Lenis) {
    return null;
  }
  const lenis = new window.Lenis({
    lerp: 0.08,
    wheelMultiplier: 0.86,
    smoothWheel: true,
  });
  function raf(time) {
    lenis.raf(time);
    requestAnimationFrame(raf);
  }
  requestAnimationFrame(raf);
  return lenis;
}

function initMotion() {
  const gsap = window.gsap;
  const ScrollTrigger = window.ScrollTrigger;

  if (reducedMotion || !gsap || !ScrollTrigger) {
    document.querySelectorAll(".reveal").forEach((node) => {
      node.style.opacity = "1";
      node.style.transform = "none";
    });
    setLiteStep(3);
    setCountdownFromProgress(1);
    return;
  }

  gsap.registerPlugin(ScrollTrigger);
  gsap.to(".hero-device", {
    opacity: 1,
    y: 0,
    duration: 0.75,
    ease: "power3.out",
    delay: 0.08,
  });
  gsap.utils.toArray(".reveal").forEach((node) => {
    gsap.to(node, {
      opacity: 1,
      y: 0,
      duration: 0.7,
      ease: "power3.out",
      scrollTrigger: {
        trigger: node,
        start: "top 86%",
        once: true,
      },
    });
  });

  if (window.matchMedia("(min-width: 721px)").matches) {
    ScrollTrigger.create({
      trigger: "#lite",
      start: "top top",
      end: "+=2800",
      pin: ".lite-pin",
      scrub: true,
      onUpdate: (self) => {
        const step = Math.min(3, Math.floor(self.progress * 4));
        setLiteStep(step);
        setCountdownFromProgress(self.progress);
      },
    });
  } else {
    setLiteStep(3);
    setCountdownFromProgress(1);
  }

  gsap.utils.toArray(".parallax-shot").forEach((image, index) => {
    gsap.to(image, {
      yPercent: index % 2 === 0 ? -5 : 5,
      ease: "none",
      scrollTrigger: {
        trigger: image,
        start: "top bottom",
        end: "bottom top",
        scrub: true,
      },
    });
  });
}

mountShotSlots();
mountGallery("showcase", 11);
mountGallery("privacy", 8);
document.querySelector("[data-year]").textContent = String(new Date().getFullYear());
initHeader();
initTilt();
initSmoothScroll();
initMotion();
