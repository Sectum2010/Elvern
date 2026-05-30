import { useEffect, useRef, useState } from "react";


export const REFRESH_SWEEP_MS = 1100;


export function RefreshSweepOverlay({ active }) {
  if (!active) {
    return null;
  }
  return (
    <svg
      aria-hidden="true"
      className="refresh-status-sweep-button__sweep"
      focusable="false"
      preserveAspectRatio="none"
      viewBox="0 0 100 40"
    >
      <rect
        height="35.2"
        pathLength="100"
        rx="17.6"
        ry="17.6"
        width="95.2"
        x="2.4"
        y="2.4"
      />
    </svg>
  );
}


export function RefreshSweepButton({
  children,
  className = "",
  disabled = false,
  onClick,
  type = "button",
  ...buttonProps
}) {
  const [sweepActive, setSweepActive] = useState(false);
  const sweepTimerRef = useRef(0);

  function startSweep() {
    if (sweepTimerRef.current) {
      window.clearTimeout(sweepTimerRef.current);
      sweepTimerRef.current = 0;
    }
    setSweepActive(false);
    window.requestAnimationFrame(() => {
      setSweepActive(true);
      sweepTimerRef.current = window.setTimeout(() => {
        setSweepActive(false);
        sweepTimerRef.current = 0;
      }, REFRESH_SWEEP_MS);
    });
  }

  function handleClick(event) {
    startSweep();
    onClick?.(event);
  }

  useEffect(() => () => {
    if (sweepTimerRef.current) {
      window.clearTimeout(sweepTimerRef.current);
    }
  }, []);

  return (
    <button
      {...buttonProps}
      className={[
        className,
        "refresh-status-sweep-button",
        sweepActive ? "refresh-status-sweep-button--active" : "",
      ].filter(Boolean).join(" ")}
      disabled={disabled}
      onClick={handleClick}
      type={type}
    >
      {children}
      <RefreshSweepOverlay active={sweepActive} />
    </button>
  );
}
