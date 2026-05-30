import { useEffect, useRef, useState } from "react";


export const REFRESH_SWEEP_MS = 1100;
const REFRESH_SWEEP_STROKE_WIDTH = 4.2;
const REFRESH_SWEEP_INSET = (REFRESH_SWEEP_STROKE_WIDTH / 2) + 0.3;


function buildSweepGeometry(buttonElement) {
  const bounds = buttonElement?.getBoundingClientRect?.();
  const width = Math.max(24, Number(bounds?.width) || 100);
  const height = Math.max(24, Number(bounds?.height) || 40);
  const styles = buttonElement ? window.getComputedStyle(buttonElement) : null;
  const borderRadius = Number.parseFloat(styles?.borderTopLeftRadius || "") || height / 2;
  const x0 = REFRESH_SWEEP_INSET;
  const y0 = REFRESH_SWEEP_INSET;
  const x1 = Math.max(x0 + 1, width - REFRESH_SWEEP_INSET);
  const y1 = Math.max(y0 + 1, height - REFRESH_SWEEP_INSET);
  const pathWidth = x1 - x0;
  const pathHeight = y1 - y0;
  const radius = Math.max(
    0,
    Math.min(borderRadius - REFRESH_SWEEP_INSET, pathWidth / 2, pathHeight / 2),
  );
  const startX = x0 + pathWidth / 2;
  const length = (2 * (pathWidth + pathHeight - (4 * radius))) + (2 * Math.PI * radius);
  const d = [
    `M ${startX.toFixed(2)} ${y0.toFixed(2)}`,
    `H ${(x1 - radius).toFixed(2)}`,
    `A ${radius.toFixed(2)} ${radius.toFixed(2)} 0 0 1 ${x1.toFixed(2)} ${(y0 + radius).toFixed(2)}`,
    `V ${(y1 - radius).toFixed(2)}`,
    `A ${radius.toFixed(2)} ${radius.toFixed(2)} 0 0 1 ${(x1 - radius).toFixed(2)} ${y1.toFixed(2)}`,
    `H ${(x0 + radius).toFixed(2)}`,
    `A ${radius.toFixed(2)} ${radius.toFixed(2)} 0 0 1 ${x0.toFixed(2)} ${(y1 - radius).toFixed(2)}`,
    `V ${(y0 + radius).toFixed(2)}`,
    `A ${radius.toFixed(2)} ${radius.toFixed(2)} 0 0 1 ${(x0 + radius).toFixed(2)} ${y0.toFixed(2)}`,
    `H ${startX.toFixed(2)}`,
  ].join(" ");

  return {
    d,
    height: Number(height.toFixed(2)),
    length: Number(Math.max(length, 1).toFixed(2)),
    width: Number(width.toFixed(2)),
  };
}


export function RefreshSweepOverlay({ active, geometry }) {
  if (!active) {
    return null;
  }
  return (
    <svg
      aria-hidden="true"
      className="refresh-status-sweep-button__sweep"
      focusable="false"
      preserveAspectRatio="none"
      viewBox={`0 0 ${geometry.width} ${geometry.height}`}
    >
      <path
        d={geometry.d}
        style={{ "--refresh-sweep-length": geometry.length }}
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
  const [sweepGeometry, setSweepGeometry] = useState(() => buildSweepGeometry(null));
  const buttonRef = useRef(null);
  const sweepTimerRef = useRef(0);

  function startSweep() {
    if (sweepTimerRef.current) {
      window.clearTimeout(sweepTimerRef.current);
      sweepTimerRef.current = 0;
    }
    setSweepGeometry(buildSweepGeometry(buttonRef.current));
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
      ref={buttonRef}
      type={type}
    >
      {children}
      <RefreshSweepOverlay active={sweepActive} geometry={sweepGeometry} />
    </button>
  );
}
