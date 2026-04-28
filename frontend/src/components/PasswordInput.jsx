import { useRef, useState } from "react";

function EyeIcon({ struck = false }) {
  return (
    <svg aria-hidden="true" fill="none" height="18" viewBox="0 0 24 24" width="18">
      <path
        d="M2.25 12S5.625 5.25 12 5.25 21.75 12 21.75 12 18.375 18.75 12 18.75 2.25 12 2.25 12Z"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.7"
      />
      <path
        d="M15 12A3 3 0 1 1 9 12A3 3 0 0 1 15 12Z"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.7"
      />
      {struck ? (
        <path
          d="M4.5 19.5L19.5 4.5"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.7"
        />
      ) : null}
    </svg>
  );
}

export function PasswordInput({
  className = "",
  showLabel = "Show password",
  hideLabel = "Hide password",
  ...inputProps
}) {
  const [visible, setVisible] = useState(false);
  const disabled = Boolean(inputProps.disabled);
  const inputRef = useRef(null);
  const selectionRef = useRef({ start: null, end: null, direction: "none" });

  function handleToggleMouseDown(event) {
    const input = inputRef.current;
    if (input) {
      selectionRef.current = {
        start: typeof input.selectionStart === "number" ? input.selectionStart : null,
        end: typeof input.selectionEnd === "number" ? input.selectionEnd : null,
        direction: input.selectionDirection || "none",
      };
    }
    event.preventDefault();
  }

  function handleToggleClick() {
    const input = inputRef.current;
    const shouldRestoreFocus = typeof document !== "undefined" && document.activeElement === input;

    setVisible((current) => !current);

    if (!shouldRestoreFocus || typeof window === "undefined") {
      return;
    }

    window.requestAnimationFrame(() => {
      const field = inputRef.current;
      if (!field) {
        return;
      }
      field.focus({ preventScroll: true });
      const { start, end, direction } = selectionRef.current;
      if (typeof start === "number" && typeof end === "number" && typeof field.setSelectionRange === "function") {
        try {
          field.setSelectionRange(start, end, direction);
        } catch {
          // Some browsers may reject selection restoration for specific input states.
        }
      }
    });
  }

  return (
    <div className="password-input">
      <input
        {...inputProps}
        className={["password-input__field", className].filter(Boolean).join(" ")}
        ref={inputRef}
        type={visible ? "text" : "password"}
      />
      <button
        aria-label={visible ? hideLabel : showLabel}
        aria-pressed={visible}
        className="password-input__toggle"
        disabled={disabled}
        onClick={handleToggleClick}
        onMouseDown={handleToggleMouseDown}
        type="button"
      >
        <EyeIcon struck={!visible} />
      </button>
    </div>
  );
}
