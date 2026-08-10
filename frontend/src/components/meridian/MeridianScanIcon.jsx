export function MeridianScanIcon({ className = "", spinning = false }) {
  return (
    <svg
      aria-hidden="true"
      className={[
        "meridian-scan-icon",
        spinning ? "is-spinning" : "",
        className,
      ].filter(Boolean).join(" ")}
      fill="none"
      height="14"
      viewBox="0 0 24 24"
      width="14"
    >
      <path d="M4.5 10.5a7.8 7.8 0 0113.6-3.6" />
      <path d="M19.5 13.5a7.8 7.8 0 01-13.6 3.6" />
      <path d="M18.3 3.5v3.6h-3.6" />
      <path d="M5.7 20.5v-3.6h3.6" />
    </svg>
  );
}
