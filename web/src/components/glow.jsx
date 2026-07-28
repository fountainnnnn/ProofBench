/* Glow-card marketing grammar — the shared primitives every marketing/glow
   section is built from, so the sections read as siblings. Geometry and colour
   live in index.css (.pb-glow-*, the glow token layer); this module is the
   React surface: one section header, one card, the ambient orb, a scroll
   reveal, and the detaching-nav hook. */

import { useEffect, useRef, useState } from "react";

/* Centered header: gradient eyebrow, weight-500 display title, one-or-two-line
   subtitle. `align="left"` for hero-side headers that are not centered. */
export function SectionHeader({ eyebrow, title, subtitle, align = "center", className = "", id }) {
  const alignment = align === "left" ? "text-left" : "mx-auto max-w-[46rem] text-center";
  return (
    <div className={`${alignment} ${className}`}>
      {eyebrow && <span className="pb-eyebrow-glow">{eyebrow}</span>}
      <h2 id={id} className={`pb-glow-title ${eyebrow ? "mt-4" : ""}`}>
        {title}
      </h2>
      {subtitle && <p className="pb-glow-sub mx-auto mt-4 max-w-[42rem]">{subtitle}</p>}
    </div>
  );
}

/* A glow card. `glow` picks one of the three cycling hue pairings; callers pass
   the index so adjacent cards never match. */
export function GlowCard({ glow = 0, className = "", children, as: Tag = "div", ...rest }) {
  return (
    <Tag className={`pb-glow-card ${className}`} data-glow={((glow % 3) + 3) % 3} {...rest}>
      {children}
    </Tag>
  );
}

/* One ambient orb drifting behind the page. Fixed, aria-hidden, non-interactive,
   frozen under reduced motion. `offset` nudges a second, fainter instance. */
export function AmbientOrb({ style }) {
  return <div aria-hidden="true" className="pb-glow-orb" style={style} />;
}

/* Plays a one-time rise as the wrapped block scrolls into view. Progressive
   enhancement: reduced-motion and no-observer paths show the content outright
   (see the CSS). */
export function Reveal({ children, className = "", as: Tag = "div", delay = 0, ...rest }) {
  const ref = useRef(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    if (typeof IntersectionObserver === "undefined") {
      setShown(true);
      return undefined;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setShown(true);
            observer.disconnect();
          }
        }
      },
      { threshold: 0.15 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <Tag
      ref={ref}
      className={`pb-reveal ${className}`}
      data-shown={shown ? "true" : "false"}
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
      {...rest}
    >
      {children}
    </Tag>
  );
}

/* True once the page has scrolled past `at` px — drives the nav's detach from a
   transparent bar into a floating glass island. */
export function useStuckNav(at = 48) {
  const [stuck, setStuck] = useState(false);
  useEffect(() => {
    const onScroll = () => setStuck(window.scrollY > at);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [at]);
  return stuck;
}
