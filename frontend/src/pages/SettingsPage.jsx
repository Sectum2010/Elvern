import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { apiRequest } from "../lib/api";
import {
  formatGoogleConnectionHealthLabel,
  formatGoogleDriveSetupLabel,
} from "../lib/cloudSyncStatus";
import { startGoogleDriveReconnect } from "../lib/providerAuth";
import {
  BACKGROUND_PRESETS,
  DEFAULT_BACKGROUND_SETTINGS,
  buildBackgroundPreviewStyle,
  deriveGradientColorsFromSingleColor,
  deriveGradientEndFromSingleColor,
  getBackgroundPickerColorAtPosition,
  getBackgroundPickerPositionFromColor,
  normalizeUserBackgroundSettings,
} from "../lib/userBackground";
import {
  readPersistedPanelState,
  writePersistedPanelState,
} from "../lib/persistedPanelState";
import { RefreshSweepButton } from "../components/RefreshSweepButton";

const USER_SETTINGS_CHANGED_EVENT = "elvern:user-settings-changed";

const SETTINGS_SECTIONS = [
  { key: "preferences", label: "Preferences", icon: "preferences" },
  { key: "display", label: "Display", icon: "display" },
  { key: "libraries", label: "Libraries", icon: "libraries" },
  { key: "hidden", label: "Hidden", icon: "hidden" },
  { key: "advanced", label: "Advanced", icon: "advanced" },
];
const SETTINGS_SECTION_KEYS = SETTINGS_SECTIONS.map((section) => section.key);
const SETTINGS_ACTIVE_SECTION_STORAGE_KEY = "elvern:settings-active-section";
const AGE_REQUIREMENT_OPTIONS = [null, ...Array.from({ length: 18 }, (_, index) => index + 1)];

const POSTER_CARD_APPEARANCE_OPTIONS = [
  { value: "classic", label: "Classic" },
  { value: "modern", label: "Modern" },
  { value: "clean", label: "Clean" },
];

const POSTER_DISPLAY_WIDTH_OPTIONS = [
  { value: "800", label: "800 px" },
  { value: "1000", label: "1000 px" },
  { value: "1200", label: "1200 px" },
  { value: "1400", label: "1400 px" },
  { value: "1600", label: "1600 px" },
  { value: "1800", label: "1800 px" },
  { value: "2000", label: "2000 px" },
  { value: "2200", label: "2200 px" },
  { value: "original", label: "Original / No upperbound" },
];

const BACKGROUND_PHOTO_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);


function formatAgeRequirement(value) {
  if (value == null || value === "") {
    return "None";
  }
  const age = Number(value);
  if (!Number.isFinite(age)) {
    return "None";
  }
  return age >= 18 ? "18+" : String(age);
}


function getBackgroundColorPickerValue(backgroundDraft) {
  if (backgroundDraft.background_mode === "solid") {
    return backgroundDraft.background_solid_color || DEFAULT_BACKGROUND_SETTINGS.background_solid_color;
  }
  return backgroundDraft.background_gradient_start || DEFAULT_BACKGROUND_SETTINGS.background_gradient_start;
}


function BackgroundColorPicker({ color, disabled, mode, onPick }) {
  const pickerRef = useRef(null);
  const canvasRef = useRef(null);
  const lastPickedColorRef = useRef("");
  const [dragging, setDragging] = useState(false);
  const [pickerPosition, setPickerPosition] = useState(() => getBackgroundPickerPositionFromColor(color));
  const position = pickerPosition;
  const pickerStyle = {
    "--settings-background-picker-x": `${position.x * 100}%`,
    "--settings-background-picker-y": `${position.y * 100}%`,
    "--settings-background-picker-color": color,
  };

  useEffect(() => {
    if (color === lastPickedColorRef.current) {
      return;
    }
    setPickerPosition(getBackgroundPickerPositionFromColor(color));
  }, [color]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    if (typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent || "")) {
      return;
    }
    let context;
    try {
      context = canvas.getContext?.("2d", { alpha: false });
    } catch {
      return;
    }
    if (!context) {
      return;
    }
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(360, Math.round(rect.width || 560));
    const height = Math.max(220, Math.round(rect.height || 288));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    const imageData = context.createImageData(width, height);
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const colorHex = getBackgroundPickerColorAtPosition(x / (width - 1 || 1), y / (height - 1 || 1));
        const offset = (y * width + x) * 4;
        imageData.data[offset] = parseInt(colorHex.slice(1, 3), 16);
        imageData.data[offset + 1] = parseInt(colorHex.slice(3, 5), 16);
        imageData.data[offset + 2] = parseInt(colorHex.slice(5, 7), 16);
        imageData.data[offset + 3] = 255;
      }
    }
    context.putImageData(imageData, 0, 0);
  }, []);

  function pickFromPoint(clientX, clientY) {
    if (disabled) {
      return;
    }
    const rect = pickerRef.current?.getBoundingClientRect();
    if (!rect) {
      return;
    }
    const width = rect.width || 1;
    const height = rect.height || 1;
    const nextPosition = {
      x: Math.max(0, Math.min(1, (clientX - rect.left) / width)),
      y: Math.max(0, Math.min(1, (clientY - rect.top) / height)),
    };
    const nextColor = getBackgroundPickerColorAtPosition(nextPosition.x, nextPosition.y);
    lastPickedColorRef.current = nextColor;
    setPickerPosition(nextPosition);
    onPick(nextColor);
  }

  function handlePointerDown(event) {
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setDragging(true);
    pickFromPoint(event.clientX, event.clientY);
  }

  function handlePointerMove(event) {
    if (event.buttons !== 1) {
      return;
    }
    pickFromPoint(event.clientX, event.clientY);
  }

  function handlePointerEnd(event) {
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    setDragging(false);
  }

  function handleKeyDown(event) {
    if (disabled) {
      return;
    }
    const step = event.shiftKey ? 0.08 : 0.035;
    let nextX = position.x;
    let nextY = position.y;
    if (event.key === "ArrowLeft") {
      nextX -= step;
    } else if (event.key === "ArrowRight") {
      nextX += step;
    } else if (event.key === "ArrowUp") {
      nextY -= step;
    } else if (event.key === "ArrowDown") {
      nextY += step;
    } else {
      return;
    }
    event.preventDefault();
    const nextPosition = {
      x: Math.max(0, Math.min(1, nextX)),
      y: Math.max(0, Math.min(1, nextY)),
    };
    const nextColor = getBackgroundPickerColorAtPosition(nextPosition.x, nextPosition.y);
    lastPickedColorRef.current = nextColor;
    setPickerPosition(nextPosition);
    onPick(nextColor);
  }

  return (
    <div
      aria-label={mode === "solid" ? "Solid background color picker" : "Gradient background color picker"}
      aria-valuetext={color}
      className="settings-background-color-picker"
      onKeyDown={handleKeyDown}
      ref={pickerRef}
      role="slider"
      style={pickerStyle}
      tabIndex={disabled ? -1 : 0}
    >
      <canvas
        aria-hidden="true"
        className="settings-background-color-picker__canvas"
        ref={canvasRef}
      />
      <span
        className={[
          "settings-background-color-picker__cursor",
          dragging ? "settings-background-color-picker__cursor--dragging" : "",
        ].filter(Boolean).join(" ")}
        aria-hidden="true"
        onPointerCancel={handlePointerEnd}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
      />
    </div>
  );
}


function SettingsSegmentedControl({ ariaLabel, disabled, onChange, options, value }) {
  const controlRef = useRef(null);
  const draggingRef = useRef(false);
  const ignoreNextClickRef = useRef(false);
  const dragBoundsRef = useRef({ clientX: 0, min: 0, max: 0 });
  const [dragging, setDragging] = useState(false);
  const [dragOffset, setDragOffset] = useState(0);
  const [dragPreviewValue, setDragPreviewValue] = useState(null);

  function getValueFromPoint(clientX) {
    const rect = controlRef.current?.getBoundingClientRect();
    if (!rect || !options.length) {
      return value;
    }
    const ratio = Math.max(0, Math.min(0.999, (clientX - rect.left) / (rect.width || 1)));
    const index = Math.max(0, Math.min(options.length - 1, Math.floor(ratio * options.length)));
    return options[index]?.value || value;
  }

  function handleActivePointerDown(event) {
    if (disabled) {
      return;
    }
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const controlRect = controlRef.current?.getBoundingClientRect();
    const buttonRect = event.currentTarget.getBoundingClientRect();
    dragBoundsRef.current = {
      clientX: event.clientX,
      min: controlRect ? controlRect.left - buttonRect.left : 0,
      max: controlRect ? controlRect.right - buttonRect.right : 0,
    };
    draggingRef.current = true;
    ignoreNextClickRef.current = true;
    setDragOffset(0);
    setDragPreviewValue(value);
    setDragging(true);
  }

  function handleActivePointerMove(event) {
    if (!draggingRef.current) {
      return;
    }
    const bounds = dragBoundsRef.current;
    const nextOffset = Math.max(bounds.min, Math.min(bounds.max, event.clientX - bounds.clientX));
    setDragOffset(nextOffset);
    setDragPreviewValue(getValueFromPoint(event.clientX));
  }

  function handleActivePointerUp(event) {
    if (!draggingRef.current) {
      return;
    }
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    draggingRef.current = false;
    setDragging(false);
    setDragOffset(0);
    const nextValue = getValueFromPoint(event.clientX);
    setDragPreviewValue(null);
    onChange(nextValue);
    window.setTimeout(() => {
      ignoreNextClickRef.current = false;
    }, 120);
  }

  function handleActivePointerCancel(event) {
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    draggingRef.current = false;
    ignoreNextClickRef.current = false;
    setDragging(false);
    setDragOffset(0);
    setDragPreviewValue(null);
  }

  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === value));
  const controlStyle = {
    "--settings-segmented-count": options.length,
    "--settings-segmented-index": selectedIndex,
    "--settings-segmented-drag-x": dragging ? `${dragOffset}px` : "0px",
  };

  return (
    <div
      className={[
        "settings-segmented-control",
        dragging ? "settings-segmented-control--dragging" : "",
      ].filter(Boolean).join(" ")}
      role="radiogroup"
      aria-label={ariaLabel}
      ref={controlRef}
      style={controlStyle}
    >
      <span
        aria-hidden="true"
        className={[
          "settings-segmented-control__indicator",
          dragging ? "settings-segmented-control__indicator--dragging" : "",
        ].filter(Boolean).join(" ")}
      />
      {options.map((option) => {
        const isSelected = value === option.value;
        const isPreviewSelected = dragging && dragPreviewValue === option.value;
        const isVisuallySelected = dragging ? isPreviewSelected : isSelected;
        const isCurrentLabel = dragging ? isPreviewSelected : isSelected;
        return (
          <button
            aria-checked={isSelected}
            className={[
              "settings-segmented-control__button",
              isCurrentLabel ? "settings-segmented-control__button--current" : "",
              isVisuallySelected ? "settings-segmented-control__button--active" : "",
              isSelected && dragging ? "settings-segmented-control__button--dragging" : "",
            ].filter(Boolean).join(" ")}
            disabled={disabled}
            key={option.value}
            onClick={(event) => {
              if (ignoreNextClickRef.current) {
                event.preventDefault();
                ignoreNextClickRef.current = false;
                return;
              }
              onChange(option.value);
            }}
            onPointerCancel={isSelected ? handleActivePointerCancel : undefined}
            onPointerDown={isSelected ? handleActivePointerDown : undefined}
            onPointerMove={isSelected ? handleActivePointerMove : undefined}
            onPointerUp={isSelected ? handleActivePointerUp : undefined}
            role="radio"
            type="button"
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}


function normalizePosterCardAppearance(value) {
  if (value === "modern" || value === "clean") {
    return value;
  }
  return "classic";
}


function normalizePosterDisplayWidth(value) {
  const normalized = String(value || "1400").toLowerCase();
  return POSTER_DISPLAY_WIDTH_OPTIONS.some((option) => option.value === normalized)
    ? normalized
    : "1400";
}


function SettingsSectionIcon({ icon }) {
  if (icon === "display") {
    return (
      <svg aria-hidden="true" className="admin-nav-card__icon-svg" viewBox="0 0 24 24">
        <rect x="4" y="5" width="16" height="11" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
        <path d="M9 20h6M12 16v4" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
        <circle cx="9" cy="9" r="1.4" fill="currentColor" />
        <path d="M7.5 13l2.7-2.5 2.1 1.8 1.3-1.2 2.9 2.9" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
      </svg>
    );
  }
  if (icon === "libraries") {
    return (
      <svg aria-hidden="true" className="admin-nav-card__icon-svg" viewBox="0 0 24 24">
        <path d="M5 6.5h14M5 12h14M5 17.5h14" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
        <path d="M7 4.5v4M11 10v4M16 15.5v4" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
      </svg>
    );
  }
  if (icon === "hidden") {
    return (
      <svg aria-hidden="true" className="admin-nav-card__icon-svg" viewBox="0 0 24 24">
        <path d="M3.5 12s3-5 8.5-5 8.5 5 8.5 5a13 13 0 0 1-2.6 2.9M14.2 14.2A3 3 0 0 1 9.8 9.8M5 5l14 14" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
      </svg>
    );
  }
  if (icon === "advanced") {
    return (
      <svg aria-hidden="true" className="admin-nav-card__icon-svg" viewBox="0 0 24 24">
        <path d="M12 8.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7z" fill="none" stroke="currentColor" strokeWidth="1.8" />
        <path d="M12 3.5v2.1M12 18.4v2.1M4.6 7.8l1.8 1M17.6 15.2l1.8 1M4.6 16.2l1.8-1M17.6 8.8l1.8-1" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
      </svg>
    );
  }
  return (
    <svg aria-hidden="true" className="admin-nav-card__icon-svg" viewBox="0 0 24 24">
      <path d="M5 7h14M7 12h10M9 17h6" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
      <circle cx="8" cy="7" r="1.7" fill="currentColor" />
      <circle cx="16" cy="12" r="1.7" fill="currentColor" />
      <circle cx="11" cy="17" r="1.7" fill="currentColor" />
    </svg>
  );
}


function StatusRow({ label, value }) {
  return (
    <div className="status-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}


function validatePosterReferenceLocationInput(value) {
  const candidate = String(value || "").trim();
  if (!candidate) {
    return "";
  }
  if (candidate.startsWith("/")) {
    return "";
  }
  try {
    const parsed = new URL(candidate);
    if (parsed.protocol !== "file:") {
      return "Use an absolute Linux path or file:// URI.";
    }
    if (parsed.host && parsed.host.toLowerCase() !== "localhost") {
      return "Remote file:// authorities are not supported here. Mount the directory locally and use a Linux path instead.";
    }
    if (!parsed.pathname.startsWith("/")) {
      return "Poster reference location must resolve to an absolute Linux directory.";
    }
    return "";
  } catch {
    return "Use an absolute Linux path or file:// URI.";
  }
}


function detectSettingsBrowsePlatform() {
  if (typeof navigator === "undefined") {
    return "linux";
  }
  const agent = (navigator.userAgent || "").toLowerCase();
  const platform = (navigator.platform || "").toLowerCase();
  const maxTouchPoints = Number(navigator.maxTouchPoints || 0);
  const iPadDesktopClassAgent =
    maxTouchPoints > 1 && (agent.includes("macintosh") || platform.includes("mac"));

  if (agent.includes("iphone") || agent.includes("ipod")) {
    return "iphone";
  }
  if (agent.includes("ipad") || iPadDesktopClassAgent) {
    return "ipad";
  }
  if (agent.includes("android")) {
    return "android";
  }
  if (agent.includes("windows")) {
    return "windows";
  }
  if (agent.includes("macintosh") || (agent.includes("mac os x") && !agent.includes("iphone") && !agent.includes("ipad"))) {
    return "mac";
  }
  if (agent.includes("linux") || platform.includes("linux") || agent.includes("x11")) {
    return "linux";
  }
  return "linux";
}


function isSettingsLocalDevelopmentLoopback(platform) {
  if (typeof window === "undefined" || platform !== "linux") {
    return false;
  }
  const host = (window.location.hostname || "").toLowerCase();
  return host === "localhost" || host === "127.0.0.1";
}


function formatCloudTimestamp(value) {
  if (!value) {
    return "Never";
  }
  try {
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return String(value);
  }
}


function sortCloudSources(sources) {
  return [...sources].sort((left, right) => {
    const leftCreatedAt = String(left?.created_at || "");
    const rightCreatedAt = String(right?.created_at || "");
    return rightCreatedAt.localeCompare(leftCreatedAt);
  });
}


function SettingsAccordionSection({ title, description, badge, isOpen, onToggle, children }) {
  return (
    <section className="settings-card settings-card--wide">
      <button
        aria-expanded={isOpen}
        className="settings-disclosure__summary settings-disclosure__summary--button"
        onClick={onToggle}
        type="button"
      >
        <span className="settings-disclosure__header">
          <span className="settings-disclosure__title">{title}</span>
          <span className="settings-disclosure__copy">{description}</span>
        </span>
        <span className="settings-disclosure__summary-meta">
          {badge !== null && badge !== undefined ? <span className="status-pill">{badge}</span> : null}
          <span
            aria-hidden="true"
            className={`settings-disclosure__chevron${isOpen ? " settings-disclosure__chevron--open" : ""}`}
          >
            ▾
          </span>
        </span>
      </button>
      {isOpen ? <div className="settings-disclosure__body">{children}</div> : null}
    </section>
  );
}


function DirectoryPickerModal({
  open,
  title,
  loading,
  error,
  currentPath,
  parentPath,
  directories,
  onNavigate,
  onUseCurrent,
  onClose,
}) {
  if (!open) {
    return null;
  }

  return (
    <div
      aria-labelledby="settings-directory-picker-title"
      aria-modal="true"
      className="browser-resume-modal"
      role="dialog"
    >
      <div
        aria-hidden="true"
        className="browser-resume-modal__backdrop"
        onClick={onClose}
      />
      <div className="browser-resume-modal__card settings-directory-picker__card">
        <div className="settings-directory-picker__header">
          <div className="settings-directory-picker__copy">
            <p className="eyebrow">Browse</p>
            <h2 id="settings-directory-picker-title">{title}</h2>
          </div>
          <button
            className="ghost-button ghost-button--inline"
            onClick={onClose}
            type="button"
          >
            Close
          </button>
        </div>

        <div className="settings-directory-picker__body">
          {error ? <p className="form-error">{error}</p> : null}
          <div className="status-row">
            <span>Current directory</span>
            <strong>{currentPath || "Loading..."}</strong>
          </div>
          <div className="settings-directory-picker__actions">
            <button
              className="ghost-button"
              disabled={loading || !parentPath}
              onClick={() => onNavigate(parentPath)}
              type="button"
            >
              Up one folder
            </button>
            <button
              className="primary-button"
              disabled={loading || !currentPath}
              onClick={onUseCurrent}
              type="button"
            >
              Use this folder
            </button>
          </div>
          <div className="settings-directory-picker__list">
            {loading ? <p className="page-note">Loading directories…</p> : null}
            {!loading && directories.length === 0 ? (
              <p className="page-subnote">No child directories here.</p>
            ) : null}
            {!loading
              ? directories.map((directory) => (
                  <button
                    className="settings-directory-picker__entry"
                    key={directory.path}
                    onClick={() => onNavigate(directory.path)}
                    type="button"
                  >
                    <span aria-hidden="true" className="settings-directory-picker__entry-icon">📁</span>
                    <span className="settings-directory-picker__entry-name">{directory.name}</span>
                  </button>
                ))
              : null}
          </div>
        </div>
      </div>
    </div>
  );
}


function AgeGroupManagerModal({
  open,
  loading,
  error,
  group,
  ageRequirementValue,
  saving,
  searchQuery,
  searchResults,
  searching,
  onAgeRequirementChange,
  onClose,
  onSaveAgeRequirement,
  onSearchQueryChange,
  onSearch,
  onLinkItem,
  onUnlinkItem,
}) {
  if (!open) {
    return null;
  }

  const autoCopies = group?.auto_matched_copies || [];
  const manualCopies = group?.manual_linked_copies || [];

  return (
    <div
      aria-labelledby="settings-age-group-manager-title"
      aria-modal="true"
      className="browser-resume-modal"
      role="dialog"
    >
      <div aria-hidden="true" className="browser-resume-modal__backdrop" onClick={onClose} />
      <div className="browser-resume-modal__card settings-age-group-modal">
        <div className="detail-info-modal__header">
          <div className="detail-info-modal__copy">
            <p className="eyebrow detail-info-modal__eyebrow">Admin</p>
            <h2 className="detail-info-modal__title" id="settings-age-group-manager-title">Age group</h2>
          </div>
          <button className="ghost-button ghost-button--inline detail-info-modal__close" onClick={onClose} type="button">
            Close
          </button>
        </div>

        <div className="detail-info-modal__body settings-age-group-modal__body">
          {loading ? <p className="page-subnote">Loading age group...</p> : null}
          {error ? <p className="form-error">{error}</p> : null}
          {group ? (
            <>
              <div className="settings-age-group-modal__summary">
                <div>
                  <strong>{group.display_title}</strong>
                  <small>{group.year || "Year unknown"}</small>
                </div>
                <span className="status-pill">{group.copies_count} copies</span>
              </div>

              <label className="settings-field">
                <span>
                  <strong>Age requirement</strong>
                  <small>Applies to the linked age group only.</small>
                </span>
                <select
                  className="admin-select"
                  disabled={saving}
                  onChange={(event) => onAgeRequirementChange(event.target.value)}
                  value={ageRequirementValue}
                >
                  {AGE_REQUIREMENT_OPTIONS.map((age) => (
                    <option key={age == null ? "none" : age} value={age == null ? "" : age}>
                      {formatAgeRequirement(age)}
                    </option>
                  ))}
                </select>
              </label>
              <div className="player-actions">
                <button
                  className="ghost-button ghost-button--inline"
                  disabled={saving || !group.primary_media_item_id}
                  onClick={onSaveAgeRequirement}
                  type="button"
                >
                  {saving ? "Saving..." : "Save age requirement"}
                </button>
              </div>

              <div className="settings-age-group-columns">
                <div className="settings-age-group-copy-list">
                  <h3>Auto copies</h3>
                  {autoCopies.length > 0 ? autoCopies.map((copy) => (
                    <article className="settings-age-group-copy" key={`auto-${copy.id}`}>
                      <strong>{copy.title}</strong>
                      <small>{copy.year || "Year unknown"} · {copy.source_label}</small>
                    </article>
                  )) : <p className="page-subnote">No automatic copies.</p>}
                </div>
                <div className="settings-age-group-copy-list">
                  <h3>Manual copies</h3>
                  {manualCopies.length > 0 ? manualCopies.map((copy) => (
                    <article className="settings-age-group-copy" key={`manual-${copy.id}`}>
                      <div>
                        <strong>{copy.title}</strong>
                        <small>{copy.year || "Year unknown"} · {copy.source_label}</small>
                      </div>
                      <button
                        className="ghost-button ghost-button--inline ghost-button--danger"
                        disabled={saving}
                        onClick={() => onUnlinkItem(copy.id)}
                        type="button"
                      >
                        Unlink
                      </button>
                    </article>
                  )) : <p className="page-subnote">No manual links.</p>}
                </div>
              </div>

              <div className="settings-age-group-search">
                <label className="settings-field">
                  <span>
                    <strong>Add movie</strong>
                    <small>Manual links are explicit and reversible.</small>
                  </span>
                  <input
                    className="cloud-source-form__input"
                    onChange={(event) => onSearchQueryChange(event.target.value)}
                    placeholder="Search movie title"
                    type="search"
                    value={searchQuery}
                  />
                </label>
                <button className="ghost-button ghost-button--inline" disabled={searching} onClick={onSearch} type="button">
                  {searching ? "Searching..." : "Search"}
                </button>
                {searchResults.length > 0 ? (
                  <div className="settings-age-group-search__results">
                    {searchResults.map((result) => {
                      const differs = result.automatic_age_group_key !== group.age_group_key;
                      return (
                        <article className="settings-age-group-search__result" key={`age-search-${result.id}`}>
                          <div>
                            <strong>{result.title}</strong>
                            <small>
                              {result.year || "Year unknown"}
                              {differs ? " · Auto group differs" : ""}
                            </small>
                          </div>
                          <button
                            className="ghost-button ghost-button--inline"
                            disabled={saving}
                            onClick={() => onLinkItem(result.id)}
                            type="button"
                          >
                            Link
                          </button>
                        </article>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}


function BackgroundResetConfirmModal({ open, pending, onCancel, onConfirm }) {
  if (!open) {
    return null;
  }

  return (
    <div
      aria-labelledby="settings-background-reset-modal-title"
      aria-modal="true"
      className="browser-resume-modal"
      role="dialog"
    >
      <div
        aria-hidden="true"
        className="browser-resume-modal__backdrop"
        onClick={pending ? undefined : onCancel}
      />
      <div className="browser-resume-modal__card detail-info-modal__card admin-confirm-modal settings-background-reset-modal">
        <div className="detail-info-modal__copy">
          <h2 id="settings-background-reset-modal-title" className="detail-info-modal__title">
            Reset background?
          </h2>
          <p className="page-subnote">
            This will restore the Neon background and remove any saved background photo.
          </p>
        </div>
        <div className="browser-resume-modal__actions admin-confirm-modal__actions">
          <button className="ghost-button" disabled={pending} onClick={onCancel} type="button">
            Cancel
          </button>
          <button
            className="ghost-button ghost-button--danger"
            disabled={pending}
            onClick={onConfirm}
            type="button"
          >
            Reset
          </button>
        </div>
      </div>
    </div>
  );
}


export function SettingsPage() {
  const { user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [settings, setSettings] = useState({
    hide_duplicate_movies: true,
    hide_recently_added: false,
    floating_controls_position: "bottom",
    floating_library_search_enabled: true,
    poster_card_appearance: "classic",
    poster_card_display_max_width: "1400",
    ...DEFAULT_BACKGROUND_SETTINGS,
    media_library_reference_private_value: null,
    media_library_reference_shared_default_value: "",
    media_library_reference_effective_value: "",
  });
  const [backgroundDraft, setBackgroundDraft] = useState(DEFAULT_BACKGROUND_SETTINGS);
  const [backgroundSaving, setBackgroundSaving] = useState(false);
  const [backgroundError, setBackgroundError] = useState("");
  const [backgroundResetConfirmOpen, setBackgroundResetConfirmOpen] = useState(false);
  const [activeSettingsSection, setActiveSettingsSection] = useState(() => (
    readPersistedPanelState(SETTINGS_ACTIVE_SECTION_STORAGE_KEY, SETTINGS_SECTION_KEYS, "preferences")
  ));
  const [activeSettingsButtonExpanded, setActiveSettingsButtonExpanded] = useState(true);
  const [hiddenItems, setHiddenItems] = useState([]);
  const [globalHiddenItems, setGlobalHiddenItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [restoringItemId, setRestoringItemId] = useState(null);
  const [restoringGlobalItemId, setRestoringGlobalItemId] = useState(null);
  const [movingToGlobalItemId, setMovingToGlobalItemId] = useState(null);
  const [movingToPersonalItemId, setMovingToPersonalItemId] = useState(null);
  const [sharedMediaLibraryReference, setSharedMediaLibraryReference] = useState({
    configured_value: null,
    effective_value: "",
    default_value: "",
    validation_rules: [],
  });
  const [sharedMediaLibraryReferenceInput, setSharedMediaLibraryReferenceInput] = useState("");
  const [sharedMediaLibraryReferenceSaving, setSharedMediaLibraryReferenceSaving] = useState(false);
  const [posterReference, setPosterReference] = useState({
    configured_value: null,
    effective_value: "",
    default_value: "",
    validation_rules: [],
  });
  const [posterReferenceInput, setPosterReferenceInput] = useState("");
  const [posterReferenceSaving, setPosterReferenceSaving] = useState(false);
  const [cloudLibraries, setCloudLibraries] = useState({
    google: {
      enabled: false,
      connected: false,
      connection_status: "not_configured",
      reconnect_required: false,
      provider_auth_required: false,
      account_email: null,
      account_name: null,
      stale_state_warning: null,
      status_message: "",
    },
    my_libraries: [],
    shared_libraries: [],
  });
  const [cloudBusyKey, setCloudBusyKey] = useState("");
  const [ageGroups, setAgeGroups] = useState({ items: [], total: 0 });
  const [expandedAgeGroupKeys, setExpandedAgeGroupKeys] = useState({});
  const [ageGroupDetailsByKey, setAgeGroupDetailsByKey] = useState({});
  const [ageGroupManager, setAgeGroupManager] = useState({
    open: false,
    loading: false,
    error: "",
    group: null,
    ageRequirementValue: "",
    saving: false,
    searchQuery: "",
    searchResults: [],
    searching: false,
  });
  const [myLibraryDraft, setMyLibraryDraft] = useState({
    resource_type: "folder",
    resource_id: "",
  });
  const [sharedLibraryDraft, setSharedLibraryDraft] = useState({
    resource_type: "folder",
    resource_id: "",
  });
  const [googleDriveSetup, setGoogleDriveSetup] = useState({
    https_origin: "",
    client_id: "",
    client_secret: "",
    javascript_origin: "",
    redirect_uri: "",
    callback_source: "unconfigured",
    callback_warning: null,
    configuration_state: "not_configured",
    configuration_label: "Not configured",
    status_message: "",
    missing_fields: [],
    connected: false,
    account_email: null,
    account_name: null,
    instructions: [],
  });
  const [googleDriveSetupDraft, setGoogleDriveSetupDraft] = useState({
    https_origin: "",
    client_id: "",
    client_secret: "",
  });
  const [googleDriveSetupSaving, setGoogleDriveSetupSaving] = useState(false);
  const [totpStatus, setTotpStatus] = useState({
    enabled: false,
    setup_available: false,
  });
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [openSections, setOpenSections] = useState({
    myLibraries: false,
    sharedLibraries: false,
    googleDriveSetup: false,
    mediaLibraryReference: false,
    posterReference: false,
    totpSetup: false,
  });
  const [directoryPicker, setDirectoryPicker] = useState({
    open: false,
    target: "shared-library",
    title: "",
    loading: false,
    error: "",
    current_path: "",
    parent_path: null,
    directories: [],
  });
  const [directoryPickerFallback, setDirectoryPickerFallback] = useState({
    target: "",
    reason: "",
  });
  const [nativePickerPendingTarget, setNativePickerPendingTarget] = useState("");
  const visibleCloudSources = [
    ...(cloudLibraries.my_libraries || []),
    ...(cloudLibraries.shared_libraries || []),
  ].filter((source) => !source?.hidden_for_user);
  const cloudReconnectRequired = Boolean(cloudLibraries.google?.reconnect_required);
  const googleSetupBadgeLabel = formatGoogleDriveSetupLabel(
    googleDriveSetup.configuration_state,
    googleDriveSetup.configuration_label,
  );
  const visibleCloudSourceHealthLabel = visibleCloudSources.length === 0
    ? "No sources"
    : visibleCloudSources.some((source) => source?.sync_status === "reconnect_required")
      ? "Reconnect required"
      : visibleCloudSources.some((source) => source?.sync_status === "stale" || source?.sync_status === "error")
        ? "Stale or error"
        : visibleCloudSources.some((source) => source?.sync_status === "never_synced")
          ? "Never synced"
          : "Current";

  useEffect(() => {
    let active = true;

    async function loadSettings() {
      setLoading(true);
      setError("");
      try {
        const [
          settingsPayload,
          hiddenPayload,
          globalHiddenPayload,
          mediaLibraryReferencePayload,
          posterPayload,
          cloudPayload,
          googleSetupPayload,
          totpPayload,
          ageGroupsPayload,
        ] = await Promise.all([
          apiRequest("/api/user-settings"),
          apiRequest("/api/user-hidden-items"),
          user?.role === "admin"
            ? apiRequest("/api/admin/global-hidden-items")
            : Promise.resolve({ items: [] }),
          user?.role === "admin"
            ? apiRequest("/api/admin/media-library-reference")
            : Promise.resolve(null),
          user?.role === "admin"
            ? apiRequest("/api/admin/poster-reference-location")
            : Promise.resolve(null),
          apiRequest("/api/cloud-libraries"),
          user?.role === "admin"
            ? apiRequest("/api/admin/google-drive-setup")
            : Promise.resolve(null),
          apiRequest("/api/auth/totp/status"),
          user?.role === "admin"
            ? apiRequest("/api/library/age-groups")
            : Promise.resolve({ items: [], total: 0 }),
        ]);
        if (active) {
          setSettings(settingsPayload);
          setBackgroundDraft(normalizeUserBackgroundSettings(settingsPayload));
          setHiddenItems(hiddenPayload.items || []);
          setGlobalHiddenItems(globalHiddenPayload.items || []);
          setCloudLibraries(cloudPayload);
          setAgeGroups(ageGroupsPayload || { items: [], total: 0 });
          if (user?.role === "admin" && mediaLibraryReferencePayload) {
            setSharedMediaLibraryReference(mediaLibraryReferencePayload);
            setSharedMediaLibraryReferenceInput(
              mediaLibraryReferencePayload.configured_value || mediaLibraryReferencePayload.default_value || "",
            );
          }
          if (user?.role === "admin" && posterPayload) {
            setPosterReference(posterPayload);
            setPosterReferenceInput(posterPayload.configured_value || posterPayload.default_value || "");
          }
          if (user?.role === "admin" && googleSetupPayload) {
            setGoogleDriveSetup(googleSetupPayload);
            setGoogleDriveSetupDraft({
              https_origin: googleSetupPayload.https_origin || "",
              client_id: googleSetupPayload.client_id || "",
              client_secret: googleSetupPayload.client_secret || "",
            });
          }
          setTotpStatus(totpPayload);
        }
      } catch (requestError) {
        if (active) {
          setError(requestError.message || "Failed to load settings");
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    loadSettings();
    return () => {
      active = false;
    };
  }, [user?.role]);

  useEffect(() => {
    if (user?.role !== "admin") {
      setSharedMediaLibraryReference({
        configured_value: null,
        effective_value: "",
        default_value: "",
        validation_rules: [],
      });
      setSharedMediaLibraryReferenceInput("");
      setPosterReference({
        configured_value: null,
        effective_value: "",
        default_value: "",
        validation_rules: [],
      });
      setPosterReferenceInput("");
      setGoogleDriveSetup({
        https_origin: "",
        client_id: "",
        client_secret: "",
        javascript_origin: "",
        redirect_uri: "",
        callback_source: "unconfigured",
        callback_warning: null,
        configuration_state: "not_configured",
        configuration_label: "Not configured",
        status_message: "",
        missing_fields: [],
        connected: false,
        account_email: null,
        account_name: null,
        instructions: [],
      });
      setGoogleDriveSetupDraft({
        https_origin: "",
        client_id: "",
        client_secret: "",
      });
      setAgeGroups({ items: [], total: 0 });
      setAgeGroupManager({
        open: false,
        loading: false,
        error: "",
        group: null,
        ageRequirementValue: "",
        saving: false,
        searchQuery: "",
        searchResults: [],
        searching: false,
      });
    } else {
    }
  }, [user?.role]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const section = params.get("section");
    if (!section || !SETTINGS_SECTION_KEYS.includes(section)) {
      return;
    }
    writePersistedPanelState(SETTINGS_ACTIVE_SECTION_STORAGE_KEY, section, SETTINGS_SECTION_KEYS);
    setActiveSettingsSection(section);
    setActiveSettingsButtonExpanded(true);
  }, [location.search]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const statusValue = params.get("googleDriveStatus");
    const statusMessage = params.get("googleDriveMessage");
    if (!statusValue && !statusMessage) {
      return;
    }
    if (statusValue === "connected") {
      setMessage(statusMessage || "Google Drive connected.");
      setError("");
      apiRequest("/api/cloud-libraries")
        .then((payload) => {
          setCloudLibraries(payload);
        })
        .catch(() => {});
      if (user?.role === "admin") {
        apiRequest("/api/admin/google-drive-setup")
          .then((payload) => {
            setGoogleDriveSetup(payload);
            setGoogleDriveSetupDraft({
              https_origin: payload.https_origin || "",
              client_id: payload.client_id || "",
              client_secret: payload.client_secret || "",
            });
          })
          .catch(() => {});
      }
    } else if (statusMessage) {
      setError(statusMessage);
      setMessage("");
    }
    const nextParams = new URLSearchParams(location.search);
    nextParams.delete("googleDriveStatus");
    nextParams.delete("googleDriveMessage");
    const nextSearch = nextParams.toString();
    const nextUrl = `${location.pathname}${nextSearch ? `?${nextSearch}` : ""}${location.hash || ""}`;
    window.history.replaceState({}, "", nextUrl);
  }, [location.hash, location.pathname, location.search, user?.role]);

  async function handleDuplicateToggle(event) {
    const nextValue = event.target.checked;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const payload = await apiRequest("/api/user-settings", {
        method: "PATCH",
        data: { hide_duplicate_movies: nextValue },
      });
      setSettings(payload);
      setMessage(
        nextValue
          ? "Duplicate copies are now hidden by default."
          : "All matching copies are now visible in the library.",
      );
    } catch (requestError) {
      setError(requestError.message || "Failed to update settings");
    } finally {
      setSaving(false);
    }
  }

  async function handleRecentlyAddedToggle(event) {
    const nextValue = event.target.checked;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const payload = await apiRequest("/api/user-settings", {
        method: "PATCH",
        data: { hide_recently_added: nextValue },
      });
      setSettings(payload);
      setMessage(
        nextValue
          ? "Recently added is now hidden in your library."
          : "Recently added is visible again in your library.",
      );
    } catch (requestError) {
      setError(requestError.message || "Failed to update settings");
    } finally {
      setSaving(false);
    }
  }

  async function handleFloatingControlsPositionChange(event) {
    const nextValue = event.target.value === "top" ? "top" : "bottom";
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const payload = await apiRequest("/api/user-settings", {
        method: "PATCH",
        data: { floating_controls_position: nextValue },
      });
      setSettings(payload);
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent(USER_SETTINGS_CHANGED_EVENT, { detail: payload }));
      }
      setMessage(
        nextValue === "top"
          ? "Floating controls now anchor to the top."
          : "Floating controls now anchor to the bottom.",
      );
    } catch (requestError) {
      setError(requestError.message || "Failed to update floating controls position");
    } finally {
      setSaving(false);
    }
  }

  async function handleFloatingLibrarySearchToggle(event) {
    const nextValue = event.target.checked;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const payload = await apiRequest("/api/user-settings", {
        method: "PATCH",
        data: { floating_library_search_enabled: nextValue },
      });
      setSettings(payload);
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent(USER_SETTINGS_CHANGED_EVENT, { detail: payload }));
      }
      setMessage(nextValue ? "Dynamic search button is enabled." : "Dynamic search button is disabled.");
    } catch (requestError) {
      setError(requestError.message || "Failed to update floating search setting");
    } finally {
      setSaving(false);
    }
  }

  async function handlePosterCardAppearanceChange(nextValue) {
    const normalizedValue = normalizePosterCardAppearance(nextValue);
    if (normalizedValue === normalizePosterCardAppearance(settings.poster_card_appearance)) {
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const payload = await apiRequest("/api/user-settings", {
        method: "PATCH",
        data: { poster_card_appearance: normalizedValue },
      });
      setSettings(payload);
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent(USER_SETTINGS_CHANGED_EVENT, { detail: payload }));
      }
      setMessage(
        normalizedValue === "modern"
          ? "Poster appearance set to Modern."
          : normalizedValue === "clean"
            ? "Poster appearance set to Clean."
            : "Poster appearance set to Classic.",
      );
    } catch (requestError) {
      setError(requestError.message || "Failed to update poster appearance");
    } finally {
      setSaving(false);
    }
  }

  async function handlePosterDisplayWidthChange(event) {
    const normalizedValue = normalizePosterDisplayWidth(event.target.value);
    if (normalizedValue === normalizePosterDisplayWidth(settings.poster_card_display_max_width)) {
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const payload = await apiRequest("/api/user-settings", {
        method: "PATCH",
        data: { poster_card_display_max_width: normalizedValue },
      });
      setSettings(payload);
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent(USER_SETTINGS_CHANGED_EVENT, { detail: payload }));
      }
      setMessage("Poster display quality saved.");
    } catch (requestError) {
      setError(requestError.message || "Failed to update poster display quality");
    } finally {
      setSaving(false);
    }
  }

  function applyBackgroundPayload(payload, successMessage) {
    const normalizedBackground = normalizeUserBackgroundSettings(payload);
    setSettings(payload);
    setBackgroundDraft(normalizedBackground);
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(USER_SETTINGS_CHANGED_EVENT, { detail: payload }));
    }
    setBackgroundError("");
    if (successMessage) {
      setMessage(successMessage);
    }
  }

  async function patchBackgroundSettings(data, successMessage) {
    setBackgroundSaving(true);
    setBackgroundError("");
    setError("");
    setMessage("");
    try {
      const payload = await apiRequest("/api/user-settings", {
        method: "PATCH",
        data,
      });
      applyBackgroundPayload(payload, successMessage);
    } catch (requestError) {
      setBackgroundError(requestError.message || "Failed to update background");
    } finally {
      setBackgroundSaving(false);
    }
  }

  function handleBackgroundModeChange(nextMode) {
    const mode = ["preset", "gradient", "solid", "photo"].includes(nextMode) ? nextMode : "preset";
    setBackgroundDraft((current) => ({
      ...current,
      background_mode: mode,
    }));
    setBackgroundError("");
    if (mode === "photo" && settings.background_photo_url) {
      patchBackgroundSettings({ background_mode: mode }, "Background saved.");
    }
  }

  function handleBackgroundPresetSelect(nextPreset) {
    const preset = BACKGROUND_PRESETS.some((entry) => entry.value === nextPreset) ? nextPreset : "neon";
    setBackgroundDraft((current) => ({
      ...current,
      background_mode: "preset",
      background_preset: preset,
    }));
    patchBackgroundSettings(
      {
        background_mode: "preset",
        background_preset: preset,
      },
      preset === "basic" ? "Basic background saved." : "Background preset saved.",
    );
  }

  function handleBackgroundPalettePick(color) {
    setBackgroundDraft((current) => {
      if (current.background_mode === "solid") {
        return {
          ...current,
          background_solid_color: color,
        };
      }
      return {
        ...current,
        ...deriveGradientColorsFromSingleColor(color),
      };
    });
    setBackgroundError("");
  }

  async function handleBackgroundCustomSave() {
    const draft = normalizeUserBackgroundSettings(backgroundDraft);
    if (draft.background_mode === "solid") {
      await patchBackgroundSettings(
        {
          background_mode: "solid",
          background_solid_color: draft.background_solid_color,
        },
        "Solid background saved.",
      );
      return;
    }
    const gradientEnd =
      draft.background_gradient_end === draft.background_gradient_start
        ? deriveGradientEndFromSingleColor(draft.background_gradient_start)
        : draft.background_gradient_end;
    await patchBackgroundSettings(
      {
        background_mode: "gradient",
        background_gradient_start: draft.background_gradient_start,
        background_gradient_end: gradientEnd,
        background_gradient_accent: draft.background_gradient_accent,
      },
      "Gradient background saved.",
    );
  }

  function requestBackgroundReset() {
    setBackgroundError("");
    setBackgroundResetConfirmOpen(true);
  }

  async function handleBackgroundPhotoUpload(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) {
      return;
    }
    if (!BACKGROUND_PHOTO_TYPES.has(file.type)) {
      setBackgroundError("Choose a JPEG, PNG, or WebP image.");
      return;
    }
    setBackgroundSaving(true);
    setBackgroundError("");
    setError("");
    setMessage("");
    const formData = new FormData();
    formData.append("file", file);
    try {
      const payload = await apiRequest("/api/user-settings/background-photo", {
        method: "POST",
        data: formData,
      });
      applyBackgroundPayload(payload, "Background photo saved.");
    } catch (requestError) {
      setBackgroundError(requestError.message || "Failed to upload background photo");
    } finally {
      setBackgroundSaving(false);
    }
  }

  async function handleBackgroundReset() {
    setBackgroundSaving(true);
    setBackgroundError("");
    setError("");
    setMessage("");
    try {
      const payload = await apiRequest("/api/user-settings/background-photo", {
        method: "DELETE",
      });
      applyBackgroundPayload(payload, "Background reset to Neon.");
      setBackgroundResetConfirmOpen(false);
    } catch (requestError) {
      setBackgroundError(requestError.message || "Failed to reset background");
    } finally {
      setBackgroundSaving(false);
    }
  }

  async function handleShowAgain(itemId) {
    setRestoringItemId(itemId);
    setError("");
    setMessage("");
    try {
      const payload = await apiRequest(`/api/user-hidden-items/${itemId}`, {
        method: "DELETE",
      });
      setHiddenItems((current) => current.filter((item) => item.id !== itemId));
      setMessage(payload.message || "This movie is visible again.");
    } catch (requestError) {
      setError(requestError.message || "Failed to restore hidden movie");
    } finally {
      setRestoringItemId(null);
    }
  }

  async function handleShowForEveryone(itemId) {
    setRestoringGlobalItemId(itemId);
    setError("");
    setMessage("");
    try {
      const payload = await apiRequest(`/api/admin/global-hidden-items/${itemId}`, {
        method: "DELETE",
      });
      setGlobalHiddenItems((current) => current.filter((item) => item.id !== itemId));
      setMessage(payload.message || "This movie is visible again.");
    } catch (requestError) {
      setError(requestError.message || "Failed to restore globally hidden movie");
    } finally {
      setRestoringGlobalItemId(null);
    }
  }

  async function handleHideUniversally(hiddenItem) {
    setMovingToGlobalItemId(hiddenItem.id);
    setError("");
    setMessage("");
    try {
      const payload = await apiRequest(`/api/admin/global-hidden-items/${hiddenItem.id}`, {
        method: "POST",
      });
      await apiRequest(`/api/user-hidden-items/${hiddenItem.id}`, {
        method: "DELETE",
      });
      setHiddenItems((current) => current.filter((item) => item.id !== hiddenItem.id));
      setGlobalHiddenItems((current) => {
        const existing = current.find((item) => item.id === hiddenItem.id);
        if (existing) {
          return current;
        }
        return [
          {
            ...hiddenItem,
            hidden_at: new Date().toISOString(),
          },
          ...current,
        ];
      });
      setMessage(payload.message || "This movie is hidden for everyone.");
    } catch (requestError) {
      setError(requestError.message || "Failed to hide this movie for everyone");
    } finally {
      setMovingToGlobalItemId(null);
    }
  }

  async function handleHideForMe(hiddenItem) {
    setMovingToPersonalItemId(hiddenItem.id);
    setError("");
    setMessage("");
    try {
      await apiRequest(`/api/user-hidden-items/${hiddenItem.id}`, {
        method: "POST",
      });
      const payload = await apiRequest(`/api/admin/global-hidden-items/${hiddenItem.id}`, {
        method: "DELETE",
      });
      setGlobalHiddenItems((current) => current.filter((item) => item.id !== hiddenItem.id));
      setHiddenItems((current) => {
        const existing = current.find((item) => item.id === hiddenItem.id);
        if (existing) {
          return current;
        }
        return [
          {
            ...hiddenItem,
            hidden_at: new Date().toISOString(),
          },
          ...current,
        ];
      });
      setMessage(payload.message || "This movie is now hidden only for your account.");
    } catch (requestError) {
      setError(requestError.message || "Failed to hide this movie only for your account");
    } finally {
      setMovingToPersonalItemId(null);
    }
  }

  async function handlePosterReferenceSave(event) {
    event.preventDefault();
    const validationMessage = validatePosterReferenceLocationInput(posterReferenceInput);
    if (validationMessage) {
      setError(validationMessage);
      setMessage("");
      return;
    }
    setPosterReferenceSaving(true);
    setError("");
    setMessage("");
    try {
      const payload = await apiRequest("/api/admin/poster-reference-location", {
        method: "PUT",
        data: { value: posterReferenceInput.trim() },
      });
      setPosterReference(payload);
      setPosterReferenceInput(payload.configured_value || payload.default_value || "");
      setMessage("Poster reference location saved.");
    } catch (requestError) {
      setError(requestError.message || "Failed to save poster reference location");
    } finally {
      setPosterReferenceSaving(false);
    }
  }

  async function handleSharedMediaLibraryReferenceSave(event) {
    event.preventDefault();
    setSharedMediaLibraryReferenceSaving(true);
    setError("");
    setMessage("");
    try {
      const payload = await apiRequest("/api/admin/media-library-reference", {
        method: "PUT",
        data: { value: sharedMediaLibraryReferenceInput },
      });
      setSharedMediaLibraryReference(payload);
      setSharedMediaLibraryReferenceInput(payload.configured_value || payload.default_value || "");
      setMessage("Shared local library path saved.");
    } catch (requestError) {
      setError(requestError.message || "Failed to save shared local library path");
    } finally {
      setSharedMediaLibraryReferenceSaving(false);
    }
  }

  async function loadDirectoryPicker(target, path) {
    setDirectoryPicker((current) => ({
      ...current,
      open: true,
      target,
      title: target === "poster-reference" ? "Browse poster directories" : "Browse shared local library directories",
      loading: true,
      error: "",
    }));
    try {
      const params = new URLSearchParams();
      if (path) {
        params.set("path", path);
      }
      const payload = await apiRequest(`/api/admin/local-directories?${params.toString()}`);
      setDirectoryPicker((current) => ({
        ...current,
        open: true,
        target,
        title: target === "poster-reference" ? "Browse poster directories" : "Browse shared local library directories",
        loading: false,
        error: "",
        current_path: payload.current_path || "",
        parent_path: payload.parent_path || null,
        directories: Array.isArray(payload.directories) ? payload.directories : [],
      }));
    } catch (requestError) {
      setDirectoryPicker((current) => ({
        ...current,
        open: true,
        target,
        title: target === "poster-reference" ? "Browse poster directories" : "Browse shared local library directories",
        loading: false,
        error: requestError.message || "Failed to browse server directories",
      }));
    }
  }

  async function handleOpenDirectoryPicker(target) {
    const platform = detectSettingsBrowsePlatform();
    const sameHostHint = isSettingsLocalDevelopmentLoopback(platform);
    const initialPath = target === "poster-reference"
      ? posterReferenceInput || posterReference.effective_value || posterReference.default_value || ""
      : sharedMediaLibraryReferenceInput
        || sharedMediaLibraryReference.effective_value
        || sharedMediaLibraryReference.default_value
        || "";
    setError("");
    setMessage("");
    setDirectoryPickerFallback({ target: "", reason: "" });
    if (platform !== "linux") {
      await loadDirectoryPicker(target, initialPath);
      return;
    }
    try {
      const params = new URLSearchParams({
        platform,
        same_host_hint: sameHostHint ? "1" : "0",
      });
      const capability = await apiRequest(`/api/admin/local-directory-picker/capability?${params.toString()}`);
      if (!capability?.same_host_linux) {
        await loadDirectoryPicker(target, initialPath);
        return;
      }
      if (!capability?.native_picker_supported) {
        setDirectoryPickerFallback({
          target,
          reason: capability?.reason || capability?.same_host_reason || "Native host picker is unavailable for this Linux same-host session.",
        });
        return;
      }
      setNativePickerPendingTarget(target);
      const payload = await apiRequest("/api/admin/local-directory-picker", {
        method: "POST",
        data: {
          path: initialPath,
          title: target === "poster-reference"
            ? "Select poster directory"
            : "Select shared local library directory",
          platform,
          same_host_hint: sameHostHint,
        },
      });
      if (payload?.status === "selected" && payload?.selected_path) {
        if (target === "poster-reference") {
          setPosterReferenceInput(payload.selected_path);
        } else {
          setSharedMediaLibraryReferenceInput(payload.selected_path);
        }
        setDirectoryPickerFallback({ target: "", reason: "" });
        return;
      }
      if (payload?.status === "cancelled") {
        setDirectoryPickerFallback({ target: "", reason: "" });
        return;
      }
      setDirectoryPickerFallback({
        target,
        reason: payload?.reason || "Failed to open the host directory picker.",
      });
    } catch (requestError) {
      setDirectoryPickerFallback({
        target,
        reason: requestError?.message || "Failed to determine Linux same-host native picker availability.",
      });
    } finally {
      setNativePickerPendingTarget("");
    }
  }

  async function handleOpenServerDirectoryBrowser(target) {
    const initialPath = target === "poster-reference"
      ? posterReferenceInput || posterReference.effective_value || posterReference.default_value || ""
      : sharedMediaLibraryReferenceInput
        || sharedMediaLibraryReference.effective_value
        || sharedMediaLibraryReference.default_value
        || "";
    setDirectoryPickerFallback({ target: "", reason: "" });
    await loadDirectoryPicker(target, initialPath);
  }

  function handleCloseDirectoryPicker() {
    setDirectoryPicker((current) => ({
      ...current,
      open: false,
      loading: false,
      error: "",
    }));
  }

  function handleUseDirectoryPickerCurrent() {
    if (!directoryPicker.current_path) {
      return;
    }
    if (directoryPicker.target === "poster-reference") {
      setPosterReferenceInput(directoryPicker.current_path);
    } else {
      setSharedMediaLibraryReferenceInput(directoryPicker.current_path);
    }
    handleCloseDirectoryPicker();
  }

  async function refreshCloudLibraries() {
    const payload = await apiRequest("/api/cloud-libraries");
    setCloudLibraries(payload);
    return payload;
  }

  async function refreshAgeGroups() {
    if (user?.role !== "admin") {
      return { items: [], total: 0 };
    }
    const payload = await apiRequest("/api/library/age-groups");
    setAgeGroups(payload);
    return payload;
  }

  async function fetchAgeGroupDetail(ageGroupKey) {
    return apiRequest(`/api/library/age-groups/${encodeURIComponent(ageGroupKey)}`);
  }

  async function loadAgeGroupDetail(ageGroupKey) {
    const payload = await fetchAgeGroupDetail(ageGroupKey);
    setAgeGroupManager((current) => ({
      ...current,
      loading: false,
      error: "",
      group: payload,
      ageRequirementValue: payload.age_requirement == null ? "" : String(payload.age_requirement),
    }));
    return payload;
  }

  async function handleToggleAgeGroupRow(group) {
    const ageGroupKey = group?.age_group_key;
    if (!ageGroupKey) {
      return;
    }
    const willOpen = !expandedAgeGroupKeys[ageGroupKey];
    setExpandedAgeGroupKeys((current) => ({
      ...current,
      [ageGroupKey]: willOpen,
    }));
    if (!willOpen || ageGroupDetailsByKey[ageGroupKey]?.group || ageGroupDetailsByKey[ageGroupKey]?.loading) {
      return;
    }
    setAgeGroupDetailsByKey((current) => ({
      ...current,
      [ageGroupKey]: { loading: true, error: "", group: null },
    }));
    try {
      const payload = await fetchAgeGroupDetail(ageGroupKey);
      setAgeGroupDetailsByKey((current) => ({
        ...current,
        [ageGroupKey]: { loading: false, error: "", group: payload },
      }));
    } catch (requestError) {
      setAgeGroupDetailsByKey((current) => ({
        ...current,
        [ageGroupKey]: {
          loading: false,
          error: requestError.message || "Failed to load age group",
          group: null,
        },
      }));
    }
  }

  async function handleOpenAgeGroupManager(group) {
    if (!group?.age_group_key) {
      return;
    }
    setAgeGroupManager({
      open: true,
      loading: true,
      error: "",
      group: null,
      ageRequirementValue: "",
      saving: false,
      searchQuery: "",
      searchResults: [],
      searching: false,
    });
    try {
      await loadAgeGroupDetail(group.age_group_key);
    } catch (requestError) {
      setAgeGroupManager((current) => ({
        ...current,
        loading: false,
        error: requestError.message || "Failed to load age group",
      }));
    }
  }

  async function handleSaveAgeGroupRequirement() {
    const group = ageGroupManager.group;
    if (!group?.primary_media_item_id || ageGroupManager.saving) {
      return;
    }
    setAgeGroupManager((current) => ({ ...current, saving: true, error: "" }));
    try {
      await apiRequest(`/api/library/item/${group.primary_media_item_id}/age-requirement`, {
        method: "PATCH",
        data: {
          age_requirement: ageGroupManager.ageRequirementValue === ""
            ? null
            : Number(ageGroupManager.ageRequirementValue),
        },
      });
      await refreshAgeGroups();
      await loadAgeGroupDetail(group.age_group_key);
      setMessage("Age requirement saved.");
      setError("");
    } catch (requestError) {
      setAgeGroupManager((current) => ({
        ...current,
        saving: false,
        error: requestError.message || "Failed to save age requirement",
      }));
      return;
    }
    setAgeGroupManager((current) => ({ ...current, saving: false }));
  }

  async function handleSearchAgeGroupCandidates() {
    const query = ageGroupManager.searchQuery.trim();
    setAgeGroupManager((current) => ({ ...current, searching: true, error: "" }));
    try {
      const params = new URLSearchParams({ q: query });
      const payload = await apiRequest(`/api/library/age-groups/search?${params.toString()}`);
      setAgeGroupManager((current) => ({
        ...current,
        searching: false,
        searchResults: payload.items || [],
      }));
    } catch (requestError) {
      setAgeGroupManager((current) => ({
        ...current,
        searching: false,
        error: requestError.message || "Failed to search movies",
      }));
    }
  }

  async function handleLinkAgeGroupItem(targetMediaItemId) {
    const group = ageGroupManager.group;
    if (!group?.age_group_key || ageGroupManager.saving) {
      return;
    }
    setAgeGroupManager((current) => ({ ...current, saving: true, error: "" }));
    try {
      const payload = await apiRequest("/api/library/age-groups/link", {
        method: "POST",
        data: {
          age_group_key: group.age_group_key,
          target_media_item_id: targetMediaItemId,
        },
      });
      await refreshAgeGroups();
      setAgeGroupManager((current) => ({
        ...current,
        saving: false,
        group: payload.age_group,
        ageRequirementValue: payload.age_group?.age_requirement == null
          ? ""
          : String(payload.age_group.age_requirement),
        searchResults: [],
        searchQuery: "",
      }));
    } catch (requestError) {
      setAgeGroupManager((current) => ({
        ...current,
        saving: false,
        error: requestError.message || "Failed to link movie",
      }));
    }
  }

  async function handleUnlinkAgeGroupItem(targetMediaItemId) {
    const group = ageGroupManager.group;
    if (!group?.age_group_key || ageGroupManager.saving) {
      return;
    }
    setAgeGroupManager((current) => ({ ...current, saving: true, error: "" }));
    try {
      await apiRequest(`/api/library/age-groups/links/${targetMediaItemId}`, {
        method: "DELETE",
      });
      await refreshAgeGroups();
      await loadAgeGroupDetail(group.age_group_key);
    } catch (requestError) {
      setAgeGroupManager((current) => ({
        ...current,
        saving: false,
        error: requestError.message || "Failed to unlink movie",
      }));
      return;
    }
    setAgeGroupManager((current) => ({ ...current, saving: false }));
  }

  function buildCloudRefreshWarning(successMessage, requestError) {
    const refreshMessage = typeof requestError?.message === "string"
      ? requestError.message.trim()
      : "";
    if (refreshMessage) {
      return `${successMessage} Cloud libraries could not refresh automatically: ${refreshMessage}`;
    }
    return `${successMessage} Cloud libraries could not refresh automatically.`;
  }

  async function refreshGoogleDriveSetup() {
    if (user?.role !== "admin") {
      return null;
    }
    const payload = await apiRequest("/api/admin/google-drive-setup");
    setGoogleDriveSetup(payload);
    setGoogleDriveSetupDraft({
      https_origin: payload.https_origin || "",
      client_id: payload.client_id || "",
      client_secret: payload.client_secret || "",
    });
    return payload;
  }

  async function handleGoogleDriveSetupSave(event) {
    event.preventDefault();
    setGoogleDriveSetupSaving(true);
    setError("");
    setMessage("");
    try {
      const payload = await apiRequest("/api/admin/google-drive-setup", {
        method: "PUT",
        data: {
          https_origin: googleDriveSetupDraft.https_origin,
          client_id: googleDriveSetupDraft.client_id,
          client_secret: googleDriveSetupDraft.client_secret,
        },
      });
      setGoogleDriveSetup(payload);
      setGoogleDriveSetupDraft({
        https_origin: payload.https_origin || "",
        client_id: payload.client_id || "",
        client_secret: payload.client_secret || "",
      });
      const successMessage = payload.configuration_state === "ready"
        ? "Google Drive setup saved. You can connect Google Drive below."
        : "Google Drive setup saved.";
      try {
        await refreshCloudLibraries();
        setMessage(successMessage);
      } catch (refreshError) {
        setMessage(buildCloudRefreshWarning(successMessage, refreshError));
      }
    } catch (requestError) {
      setError(requestError.message || "Failed to save Google Drive setup");
    } finally {
      setGoogleDriveSetupSaving(false);
    }
  }

  async function handleCopyGoogleDriveCallback() {
    if (!googleDriveSetup.redirect_uri || typeof navigator === "undefined" || !navigator.clipboard?.writeText) {
      return;
    }
    try {
      await navigator.clipboard.writeText(googleDriveSetup.redirect_uri);
      setMessage("Google Drive redirect URI copied.");
      setError("");
    } catch {
      setError("Failed to copy the Google Drive redirect URI.");
      setMessage("");
    }
  }

  async function handleGoogleDriveConnect() {
    setCloudBusyKey("google-connect");
    setError("");
    setMessage("");
    try {
      await startGoogleDriveReconnect();
    } catch (requestError) {
      setError(requestError.message || "Failed to start Google Drive sign-in");
      setCloudBusyKey("");
    }
  }

  async function handleAddCloudSource(scope) {
    const isShared = scope === "shared";
    const draft = isShared ? sharedLibraryDraft : myLibraryDraft;
    const resourceId = draft.resource_id.trim();
    if (!resourceId) {
      setError("Google Drive resource ID is required.");
      setMessage("");
      return;
    }
    const busyKey = isShared ? "add-shared-library" : "add-my-library";
    setCloudBusyKey(busyKey);
    setError("");
    setMessage("");
    try {
      const created = await apiRequest("/api/cloud-libraries/sources", {
        method: "POST",
        data: {
          resource_type: draft.resource_type,
          resource_id: resourceId,
          shared: isShared,
        },
      });
      setCloudLibraries((current) => ({
        ...current,
        my_libraries: isShared ? current.my_libraries : [created, ...current.my_libraries],
        shared_libraries: isShared ? [created, ...current.shared_libraries] : current.shared_libraries,
      }));
      if (isShared) {
        setSharedLibraryDraft({ resource_type: "folder", resource_id: "" });
      } else {
        setMyLibraryDraft({ resource_type: "folder", resource_id: "" });
      }
      const successMessage = isShared
        ? "Shared library added from Google Drive."
        : "Google Drive library added.";
      try {
        await refreshCloudLibraries();
        setMessage(successMessage);
      } catch (refreshError) {
        setMessage(buildCloudRefreshWarning(successMessage, refreshError));
      }
    } catch (requestError) {
      setError(requestError.message || "Failed to add Google Drive library");
    } finally {
      setCloudBusyKey("");
    }
  }

  async function handleSharedLibraryVisibilityToggle(source) {
    const nextHidden = !source.hidden_for_user;
    const busyKey = `shared-visibility-${source.id}`;
    setCloudBusyKey(busyKey);
    setError("");
    setMessage("");
    try {
      const payload = await apiRequest(`/api/cloud-libraries/sources/${source.id}/hide`, {
        method: nextHidden ? "POST" : "DELETE",
      });
      setCloudLibraries((current) => ({
        ...current,
        shared_libraries: current.shared_libraries.map((entry) =>
          entry.id === source.id ? { ...entry, hidden_for_user: nextHidden } : entry,
        ),
      }));
      setMessage(
        payload.message
          || (nextHidden ? "This shared library is hidden for your account." : "This shared library is visible again."),
      );
    } catch (requestError) {
      setError(requestError.message || "Failed to update shared library visibility");
    } finally {
      setCloudBusyKey("");
    }
  }

  async function handleMoveCloudSource(source, nextShared) {
    const busyKey = `${nextShared ? "share-globally" : "move-to-my"}-${source.id}`;
    setCloudBusyKey(busyKey);
    setError("");
    setMessage("");
    try {
      const updated = await apiRequest(`/api/cloud-libraries/sources/${source.id}`, {
        method: "PATCH",
        data: { shared: nextShared },
      });
      setCloudLibraries((current) => {
        const nextMyLibraries = current.my_libraries.filter((entry) => entry.id !== source.id);
        const nextSharedLibraries = current.shared_libraries.filter((entry) => entry.id !== source.id);
        if (nextShared) {
          return {
            ...current,
            my_libraries: nextMyLibraries,
            shared_libraries: sortCloudSources([updated, ...nextSharedLibraries]),
          };
        }
        return {
          ...current,
          my_libraries: sortCloudSources([updated, ...nextMyLibraries]),
          shared_libraries: nextSharedLibraries,
        };
      });
      setMessage(nextShared ? "Library shared globally." : "Library moved back to My Libraries.");
      await refreshCloudLibraries();
    } catch (requestError) {
      setError(requestError.message || "Failed to move cloud library");
    } finally {
      setCloudBusyKey("");
    }
  }

  function toggleSection(sectionKey) {
    setOpenSections((current) => ({
      ...current,
      [sectionKey]: !current[sectionKey],
    }));
  }

  function handleSettingsPanelToggle(sectionKey) {
    writePersistedPanelState(SETTINGS_ACTIVE_SECTION_STORAGE_KEY, sectionKey, SETTINGS_SECTION_KEYS);
    setActiveSettingsSection((currentSection) => {
      if (currentSection === sectionKey) {
        setActiveSettingsButtonExpanded((currentExpanded) => !currentExpanded);
        return currentSection;
      }
      setActiveSettingsButtonExpanded(true);
      return sectionKey;
    });
  }

  return (
    <section className="page-section">
      <DirectoryPickerModal
        currentPath={directoryPicker.current_path}
        directories={directoryPicker.directories}
        error={directoryPicker.error}
        loading={directoryPicker.loading}
        onClose={handleCloseDirectoryPicker}
        onNavigate={(path) => loadDirectoryPicker(directoryPicker.target, path)}
        onUseCurrent={handleUseDirectoryPickerCurrent}
        open={directoryPicker.open}
        parentPath={directoryPicker.parent_path}
        title={directoryPicker.title}
      />
      <BackgroundResetConfirmModal
        onCancel={() => setBackgroundResetConfirmOpen(false)}
        onConfirm={handleBackgroundReset}
        open={backgroundResetConfirmOpen}
        pending={backgroundSaving}
      />
      <AgeGroupManagerModal
        ageRequirementValue={ageGroupManager.ageRequirementValue}
        error={ageGroupManager.error}
        group={ageGroupManager.group}
        loading={ageGroupManager.loading}
        onAgeRequirementChange={(value) =>
          setAgeGroupManager((current) => ({ ...current, ageRequirementValue: value }))
        }
        onClose={() => setAgeGroupManager((current) => ({ ...current, open: false }))}
        onLinkItem={handleLinkAgeGroupItem}
        onSaveAgeRequirement={handleSaveAgeGroupRequirement}
        onSearch={handleSearchAgeGroupCandidates}
        onSearchQueryChange={(value) =>
          setAgeGroupManager((current) => ({ ...current, searchQuery: value }))
        }
        onUnlinkItem={handleUnlinkAgeGroupItem}
        open={ageGroupManager.open}
        saving={ageGroupManager.saving}
        searchQuery={ageGroupManager.searchQuery}
        searchResults={ageGroupManager.searchResults}
        searching={ageGroupManager.searching}
      />

      <div className="admin-nav-card settings-section-nav-card" aria-label="Settings sections">
        <div className="admin-nav-card__actions settings-section-nav-card__actions" role="tablist">
          {SETTINGS_SECTIONS.map((section) => {
            const isActive = activeSettingsSection === section.key;
            return (
              <button
                aria-label={section.label}
                aria-expanded={isActive}
                aria-selected={isActive}
                className={[
                  "admin-nav-card__button",
                  isActive ? "admin-nav-card__button--active" : "",
                  isActive && activeSettingsButtonExpanded ? "admin-nav-card__button--expanded" : "",
                ].filter(Boolean).join(" ")}
                key={section.key}
                onClick={() => handleSettingsPanelToggle(section.key)}
                role="tab"
                title={section.label}
                type="button"
              >
                <span className="admin-nav-card__icon">
                  <SettingsSectionIcon icon={section.icon} />
                </span>
                <span className="admin-nav-card__label">{section.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {error ? <p className="form-error">{error}</p> : null}
      {message ? <p className="page-note">{message}</p> : null}

      {activeSettingsSection === "preferences" ? (
      <div className="settings-grid">
        <section className="settings-card">
          <h2>Your account</h2>
          <StatusRow label="Username" value={user?.username || "Unknown"} />
          <StatusRow label="Session" value={user?.session_id ? `#${user.session_id}` : "Active"} />
          <p className="page-subnote">
            Password changes are admin-managed. Contact an admin if you need a password reset.
          </p>
        </section>
      </div>
      ) : null}

      {activeSettingsSection === "display" ? (
      <div className="settings-grid settings-grid--display settings-grid--compact-columns">
        <div className="settings-grid__column">
          <section className="settings-card settings-display-card">
            <div className="settings-inline-header">
              <div>
                <h2>Poster appearance</h2>
                <p className="page-subnote">Choose how movie cards appear in your library.</p>
              </div>
            </div>
            {loading ? (
              <p className="page-subnote">Loading display preferences...</p>
            ) : (
              <div className="settings-card-stack">
                <SettingsSegmentedControl
                  ariaLabel="Poster appearance"
                  disabled={saving}
                  onChange={handlePosterCardAppearanceChange}
                  options={POSTER_CARD_APPEARANCE_OPTIONS}
                  value={normalizePosterCardAppearance(settings.poster_card_appearance)}
                />
                <label className="settings-field">
                  <span>
                    <strong>Poster display quality</strong>
                    <small>Maximum poster width used for library card images.</small>
                  </span>
                  <select
                    className="admin-select"
                    disabled={saving}
                    onChange={handlePosterDisplayWidthChange}
                    value={normalizePosterDisplayWidth(settings.poster_card_display_max_width)}
                  >
                    {POSTER_DISPLAY_WIDTH_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </label>
              </div>
            )}
          </section>

          <section className="settings-card settings-display-interface-card">
            <h2>Interface</h2>
            {loading ? (
              <p className="page-subnote">Loading interface preferences...</p>
            ) : (
              <div className="settings-card-stack">
                <label className="settings-field">
                  <span>
                    <strong>Floating island position</strong>
                    <small>Move the full floating navigation and account island away from the Dynamic Island area.</small>
                  </span>
                  <select
                    className="admin-select"
                    disabled={saving}
                    onChange={handleFloatingControlsPositionChange}
                    value={settings.floating_controls_position || "bottom"}
                  >
                    <option value="bottom">Bottom</option>
                    <option value="top">Top</option>
                  </select>
                </label>
                <label className="settings-toggle">
                  <span>
                    <strong>Dynamic search button</strong>
                    <small>Show the compact search button on Library pages.</small>
                  </span>
                  <input
                    checked={settings.floating_library_search_enabled !== false}
                    disabled={saving}
                    onChange={handleFloatingLibrarySearchToggle}
                    type="checkbox"
                  />
                </label>
              </div>
            )}
          </section>
        </div>

        <div className="settings-grid__column">
          <section className="settings-card settings-background-card">
            <div className="settings-inline-header">
              <div>
                <h2>Background</h2>
              </div>
            </div>
            {loading ? (
              <p className="page-subnote">Loading background preferences...</p>
            ) : (
              <div className="settings-card-stack settings-background-stack">
                <SettingsSegmentedControl
                  ariaLabel="Background mode"
                  disabled={backgroundSaving}
                  onChange={handleBackgroundModeChange}
                  options={[
                    { value: "preset", label: "Presets" },
                    { value: "gradient", label: "Gradient" },
                    { value: "solid", label: "Solid" },
                    { value: "photo", label: "Photo" },
                  ]}
                  value={backgroundDraft.background_mode}
                />

                {backgroundDraft.background_mode === "preset" ? (
                  <div className="settings-background-preset-grid" role="radiogroup" aria-label="Background presets">
                    {BACKGROUND_PRESETS.map((preset) => {
                      const isSelected = backgroundDraft.background_preset === preset.value;
                      return (
                        <button
                          aria-checked={isSelected}
                          className={[
                            "settings-background-preset",
                            isSelected ? "settings-background-preset--active" : "",
                          ].filter(Boolean).join(" ")}
                          disabled={backgroundSaving}
                          key={preset.value}
                          onClick={() => handleBackgroundPresetSelect(preset.value)}
                          role="radio"
                          type="button"
                        >
                          <span
                            aria-hidden="true"
                            className="settings-background-preset__swatch"
                            style={{ background: preset.swatch }}
                          />
                          <span>{preset.label}</span>
                        </button>
                      );
                    })}
                  </div>
                ) : null}

                {backgroundDraft.background_mode === "gradient" ? (
                  <div className="settings-background-picker-panel">
                    <BackgroundColorPicker
                      color={getBackgroundColorPickerValue(backgroundDraft)}
                      disabled={backgroundSaving}
                      mode="gradient"
                      onPick={handleBackgroundPalettePick}
                    />
                    <div className="settings-background-actions">
                      <button
                        className="ghost-button ghost-button--inline"
                        disabled={backgroundSaving}
                        onClick={handleBackgroundCustomSave}
                        type="button"
                      >
                        Save gradient
                      </button>
                      <button
                        className="ghost-button ghost-button--inline"
                        disabled={backgroundSaving}
                        onClick={requestBackgroundReset}
                        type="button"
                      >
                        Reset
                      </button>
                    </div>
                  </div>
                ) : null}

                {backgroundDraft.background_mode === "solid" ? (
                  <div className="settings-background-picker-panel">
                    <BackgroundColorPicker
                      color={getBackgroundColorPickerValue(backgroundDraft)}
                      disabled={backgroundSaving}
                      mode="solid"
                      onPick={handleBackgroundPalettePick}
                    />
                    <div className="settings-background-actions">
                      <button
                        className="ghost-button ghost-button--inline"
                        disabled={backgroundSaving}
                        onClick={handleBackgroundCustomSave}
                        type="button"
                      >
                        Save solid
                      </button>
                      <button
                        className="ghost-button ghost-button--inline"
                        disabled={backgroundSaving}
                        onClick={requestBackgroundReset}
                        type="button"
                      >
                        Reset
                      </button>
                    </div>
                  </div>
                ) : null}

                {backgroundDraft.background_mode === "photo" ? (
                  <div className="settings-background-photo-panel">
                    {settings.background_photo_url ? (
                      <div
                        aria-label="Background photo preview"
                        className="settings-background-preview"
                        style={buildBackgroundPreviewStyle({
                          ...backgroundDraft,
                          background_mode: "photo",
                          background_photo_url: settings.background_photo_url,
                        })}
                      >
                        <span className="settings-background-preview__shine" aria-hidden="true" />
                      </div>
                    ) : null}
                    <div className="settings-background-actions">
                      <label className="ghost-button ghost-button--inline settings-background-upload">
                        <span>{settings.background_photo_url ? "Replace photo" : "Upload photo"}</span>
                        <input
                          accept="image/jpeg,image/png,image/webp"
                          className="sr-only"
                          disabled={backgroundSaving}
                          onChange={handleBackgroundPhotoUpload}
                          type="file"
                        />
                      </label>
                      <button
                        className="ghost-button ghost-button--inline"
                        disabled={backgroundSaving}
                        onClick={requestBackgroundReset}
                        type="button"
                      >
                        Reset
                      </button>
                    </div>
                  </div>
                ) : null}

                {backgroundError ? <p className="form-error settings-background-error">{backgroundError}</p> : null}
              </div>
            )}
          </section>

          <section className="settings-card settings-display-library-card">
            <h2>Library</h2>
            {loading ? (
              <p className="page-subnote">Loading your library preferences...</p>
            ) : (
              <label className="settings-toggle">
                <span>
                  <strong>Hide Recently added</strong>
                  <small>Remove the Recently added section from your Library view.</small>
                </span>
                <input
                  checked={settings.hide_recently_added}
                  disabled={saving}
                  onChange={handleRecentlyAddedToggle}
                  type="checkbox"
                />
              </label>
            )}
          </section>
        </div>
      </div>
      ) : null}

      {activeSettingsSection === "libraries" ? (
      <div className="settings-grid">
        <section className="settings-card">
          <h2>Library</h2>
          {loading ? (
            <p className="page-subnote">Loading your library preferences...</p>
          ) : (
            <label className="settings-toggle">
              <span>
                <strong>Hide duplicate copies</strong>
                <small>Show only the highest-quality copy for the same title, year, and edition.</small>
              </span>
              <input
                checked={settings.hide_duplicate_movies}
                disabled={saving}
                onChange={handleDuplicateToggle}
                type="checkbox"
              />
            </label>
          )}
        </section>

        {user?.role === "admin" ? (
          <section className="settings-card settings-card--wide settings-age-groups-card">
            <div className="settings-inline-header">
              <div>
                <h2>Age Groups</h2>
                <p className="page-subnote">Review automatic movie age groups and explicit manual links.</p>
              </div>
              <RefreshSweepButton className="ghost-button ghost-button--inline" onClick={refreshAgeGroups} type="button">
                Refresh
              </RefreshSweepButton>
            </div>
            {loading ? (
              <p className="page-subnote">Loading age groups...</p>
            ) : (ageGroups.items || []).length > 0 ? (
              <div className="settings-age-group-list">
                {(ageGroups.items || []).map((group) => {
                  const expanded = Boolean(expandedAgeGroupKeys[group.age_group_key]);
                  const detailState = ageGroupDetailsByKey[group.age_group_key] || {};
                  const detailGroup = detailState.group;
                  const expandedCopies = [
                    ...((detailGroup?.auto_matched_copies || []).map((copy) => ({ ...copy, membership: "Auto" }))),
                    ...((detailGroup?.manual_linked_copies || []).map((copy) => ({ ...copy, membership: "Manual" }))),
                  ];
                  return (
                    <article
                      className={[
                        "settings-age-group-row",
                        expanded ? "settings-age-group-row--expanded" : "",
                      ].filter(Boolean).join(" ")}
                      key={group.age_group_key}
                    >
                      <div className="settings-age-group-row__top">
                        <button
                          aria-expanded={expanded}
                          className="settings-age-group-row__header"
                          onClick={() => handleToggleAgeGroupRow(group)}
                          type="button"
                        >
                          <div className="settings-age-group-row__copy">
                            <strong>{group.display_title}</strong>
                            <small>
                              {group.year || "Year unknown"} · {group.copies_count} copies
                              {group.manual_links_count ? ` · ${group.manual_links_count} manual` : ""}
                            </small>
                          </div>
                          <span className="status-pill">{group.age_requirement_display || formatAgeRequirement(group.age_requirement)}</span>
                          <span aria-hidden="true" className="settings-age-group-row__chevron">
                            {expanded ? "▴" : "▾"}
                          </span>
                        </button>
                        <button
                          className="ghost-button ghost-button--inline"
                          onClick={(event) => {
                            event.stopPropagation();
                            handleOpenAgeGroupManager(group);
                          }}
                          type="button"
                        >
                          Manage
                        </button>
                      </div>
                      {expanded ? (
                        <div className="settings-age-group-row__details">
                          {detailState.loading ? <p className="page-subnote">Loading copies...</p> : null}
                          {detailState.error ? <p className="form-error">{detailState.error}</p> : null}
                          {!detailState.loading && !detailState.error ? (
                            expandedCopies.length > 0 ? (
                              <div className="settings-age-group-row__movies">
                                {expandedCopies.map((copy) => (
                                  <article className="settings-age-group-copy" key={`age-row-copy-${copy.membership}-${copy.id}`}>
                                    <strong>{copy.title}</strong>
                                    <small>{copy.membership} · {copy.year || "Year unknown"} · {copy.source_label}</small>
                                  </article>
                                ))}
                              </div>
                            ) : (
                              <p className="page-subnote">No copies found.</p>
                            )
                          ) : null}
                        </div>
                      ) : null}
                    </article>
                  );
                })}
              </div>
            ) : (
              <p className="page-subnote">No age groups found yet.</p>
            )}
          </section>
        ) : null}

        <SettingsAccordionSection
          badge={cloudLibraries.my_libraries.length}
          description="Add your own Google Drive movie folders here. Personal cloud sources appear in your Library alongside DGX titles."
          isOpen={openSections.myLibraries}
          onToggle={() => toggleSection("myLibraries")}
          title="My Libraries"
        >
          {!cloudLibraries.google.enabled ? (
            <p className="page-subnote">
              {user?.role === "admin"
                ? "Finish Google Drive Setup below to enable your personal cloud libraries."
                : "Google Drive integration is not configured on this server yet."}
            </p>
          ) : (
            <div className="cloud-libraries-stack">
              <div className="cloud-connection-card">
                <div className="cloud-connection-card__copy">
                  <strong>Google Drive</strong>
                  <small>
                    {cloudLibraries.google.status_message
                      || (cloudLibraries.google.connected
                        ? `Connected as ${cloudLibraries.google.account_name || cloudLibraries.google.account_email || "Google account"}`
                        : "Connect your Google account to add Drive folders or shared drives.")}
                  </small>
                </div>
                <button
                  className="ghost-button ghost-button--inline"
                  disabled={cloudBusyKey === "google-connect"}
                  onClick={handleGoogleDriveConnect}
                  type="button"
                >
                  {cloudBusyKey === "google-connect"
                    ? "Connecting..."
                    : cloudLibraries.google.reconnect_required
                      ? "Reconnect Google Drive"
                      : cloudLibraries.google.connected
                      ? "Reconnect Google Drive"
                      : "Connect Google Drive"}
                </button>
              </div>
              {cloudReconnectRequired ? (
                <p className="form-error">
                  Reconnect Google Drive. Cloud libraries were not refreshed and may be stale until the next successful sync.
                </p>
              ) : null}

              {cloudLibraries.google.connected ? (
                <form
                  className="cloud-source-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    handleAddCloudSource("personal");
                  }}
                >
                  <label className="settings-field">
                    <span>
                      <strong>Resource type</strong>
                      <small>Choose a Google Drive folder or a shared drive ID.</small>
                    </span>
                    <select
                      className="admin-select"
                      disabled={cloudBusyKey === "add-my-library"}
                      onChange={(event) =>
                        setMyLibraryDraft((current) => ({ ...current, resource_type: event.target.value }))
                      }
                      value={myLibraryDraft.resource_type}
                    >
                      <option value="folder">Folder</option>
                      <option value="shared_drive">Shared drive</option>
                    </select>
                  </label>
                  <label className="settings-field">
                    <span>
                      <strong>Google Drive resource ID</strong>
                      <small>Paste the folder ID or shared drive ID exactly as it appears in Google Drive.</small>
                    </span>
                    <input
                      autoCapitalize="off"
                      autoCorrect="off"
                      className="cloud-source-form__input"
                      disabled={cloudBusyKey === "add-my-library"}
                      onChange={(event) =>
                        setMyLibraryDraft((current) => ({ ...current, resource_id: event.target.value }))
                      }
                      spellCheck="false"
                      type="text"
                      value={myLibraryDraft.resource_id}
                    />
                  </label>
                  <div className="player-actions">
                    <button className="primary-button" disabled={cloudBusyKey === "add-my-library"} type="submit">
                      {cloudBusyKey === "add-my-library" ? "Adding..." : "Add to My Libraries"}
                    </button>
                  </div>
                </form>
              ) : null}

              {cloudLibraries.my_libraries.length > 0 ? (
                <div className="cloud-source-list">
                  {cloudLibraries.my_libraries.map((source) => (
                    <article className="cloud-source-row" key={`my-library-${source.id}`}>
                      <div className="cloud-source-row__copy">
                        <div className="cloud-source-row__headline">
                          <strong>{source.display_name}</strong>
                          <span className="status-pill">{source.item_count} item(s)</span>
                        </div>
                        <div className="detail-list">
                          <span>{source.resource_type === "shared_drive" ? "Shared Drive" : "Folder"}</span>
                          <span>Cloud</span>
                          <span>Last synced {formatCloudTimestamp(source.last_synced_at)}</span>
                        </div>
                        {source.status_message ? <p className="form-error">{source.status_message}</p> : null}
                        {source.stale_state_warning ? <p className="page-subnote">{source.stale_state_warning}</p> : null}
                      </div>
                      {user?.role === "admin" ? (
                        <div className="cloud-source-row__actions">
                          <button
                            className="ghost-button ghost-button--inline"
                            disabled={cloudBusyKey === `share-globally-${source.id}`}
                            onClick={() => handleMoveCloudSource(source, true)}
                            type="button"
                          >
                            {cloudBusyKey === `share-globally-${source.id}` ? "Sharing..." : "Share globally"}
                          </button>
                        </div>
                      ) : null}
                    </article>
                  ))}
                </div>
              ) : (
                <p className="page-subnote">No personal cloud libraries added yet.</p>
              )}
            </div>
          )}
        </SettingsAccordionSection>

        <SettingsAccordionSection
          badge={cloudLibraries.shared_libraries.length}
          description="Admin-shared Google Drive libraries appear in every user&apos;s Library. You can still hide a shared library for your own account."
          isOpen={openSections.sharedLibraries}
          onToggle={() => toggleSection("sharedLibraries")}
          title="Shared Libraries"
        >
          <div className="cloud-libraries-stack">
            {user?.role === "admin" && cloudLibraries.google.enabled && cloudLibraries.google.connected ? (
              <form
                className="cloud-source-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  handleAddCloudSource("shared");
                }}
              >
                <label className="settings-field">
                  <span>
                    <strong>Resource type</strong>
                    <small>Choose a Google Drive folder or a shared drive ID to share globally.</small>
                  </span>
                  <select
                    className="admin-select"
                    disabled={cloudBusyKey === "add-shared-library"}
                    onChange={(event) =>
                      setSharedLibraryDraft((current) => ({ ...current, resource_type: event.target.value }))
                    }
                    value={sharedLibraryDraft.resource_type}
                  >
                    <option value="folder">Folder</option>
                    <option value="shared_drive">Shared drive</option>
                  </select>
                </label>
                <label className="settings-field">
                  <span>
                    <strong>Google Drive resource ID</strong>
                    <small>Paste the folder ID or shared drive ID you want to expose to everyone.</small>
                  </span>
                  <input
                    autoCapitalize="off"
                    autoCorrect="off"
                    className="cloud-source-form__input"
                    disabled={cloudBusyKey === "add-shared-library"}
                    onChange={(event) =>
                      setSharedLibraryDraft((current) => ({ ...current, resource_id: event.target.value }))
                    }
                    spellCheck="false"
                    type="text"
                    value={sharedLibraryDraft.resource_id}
                  />
                </label>
                <div className="player-actions">
                  <button className="primary-button" disabled={cloudBusyKey === "add-shared-library"} type="submit">
                    {cloudBusyKey === "add-shared-library" ? "Adding..." : "Add to Shared Libraries"}
                  </button>
                </div>
              </form>
            ) : null}

            {cloudLibraries.shared_libraries.length > 0 ? (
              <div className="cloud-source-list">
                {cloudLibraries.shared_libraries.map((source) => (
                  <article className="cloud-source-row" key={`shared-library-${source.id}`}>
                    <div className="cloud-source-row__copy">
                      <div className="cloud-source-row__headline">
                        <strong>{source.display_name}</strong>
                        <span className="status-pill">{source.item_count} item(s)</span>
                      </div>
                      <div className="detail-list">
                        <span>{source.resource_type === "shared_drive" ? "Shared Drive" : "Folder"}</span>
                        <span>Cloud</span>
                        {source.owner_username ? <span>Shared by {source.owner_username}</span> : null}
                        <span>Last synced {formatCloudTimestamp(source.last_synced_at)}</span>
                      </div>
                      {source.status_message ? <p className="form-error">{source.status_message}</p> : null}
                      {source.stale_state_warning ? <p className="page-subnote">{source.stale_state_warning}</p> : null}
                    </div>
                    <div className="cloud-source-row__actions">
                      {user?.role === "admin" && source.owner_username === user.username ? (
                        <button
                          className="ghost-button ghost-button--inline"
                          disabled={cloudBusyKey === `move-to-my-${source.id}` || cloudBusyKey === `shared-visibility-${source.id}`}
                          onClick={() => handleMoveCloudSource(source, false)}
                          type="button"
                        >
                          {cloudBusyKey === `move-to-my-${source.id}` ? "Moving..." : "Move to My Libraries"}
                        </button>
                      ) : null}
                      <button
                        className="ghost-button ghost-button--inline"
                        disabled={cloudBusyKey === `shared-visibility-${source.id}` || cloudBusyKey === `move-to-my-${source.id}`}
                        onClick={() => handleSharedLibraryVisibilityToggle(source)}
                        type="button"
                      >
                        {cloudBusyKey === `shared-visibility-${source.id}`
                          ? (source.hidden_for_user ? "Showing..." : "Hiding...")
                          : (source.hidden_for_user ? "Show in Library" : "Hide for me")}
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <p className="page-subnote">No shared cloud libraries have been added yet.</p>
            )}
          </div>
        </SettingsAccordionSection>

        {user?.role === "admin" ? (
          <SettingsAccordionSection
            badge={googleSetupBadgeLabel}
            description="Configure a real HTTPS Google OAuth origin for this Elvern server here. Once saved, your My Libraries and Shared Libraries sections can connect to Google Drive without editing env files manually."
            isOpen={openSections.googleDriveSetup}
            onToggle={() => toggleSection("googleDriveSetup")}
            title="Google Drive OAuth Setup"
          >
            <div className="cloud-libraries-stack">
              <div className="cloud-connection-card google-drive-setup-card">
                <div className="cloud-connection-card__copy">
                  <strong>OAuth configuration</strong>
                  <small>{googleDriveSetup.status_message}</small>
                </div>
                <div className="google-drive-setup-status-grid">
                  <StatusRow label="OAuth setup" value={googleSetupBadgeLabel} />
                  <StatusRow
                    label="Account health"
                    value={formatGoogleConnectionHealthLabel(cloudLibraries.google)}
                  />
                  <StatusRow
                    label="Source health"
                    value={visibleCloudSourceHealthLabel}
                  />
                  <StatusRow
                    label="HTTPS origin"
                    value={googleDriveSetup.missing_fields.includes("https_origin") ? "Missing" : "Configured"}
                  />
                  <StatusRow
                    label="Client ID"
                    value={googleDriveSetup.missing_fields.includes("client_id") ? "Missing" : "Configured"}
                  />
                  <StatusRow
                    label="Client Secret"
                    value={googleDriveSetup.missing_fields.includes("client_secret") ? "Missing" : "Configured"}
                  />
                </div>
                {cloudLibraries.google.status_message ? (
                  <p className={cloudReconnectRequired ? "form-error" : "page-subnote"}>
                    {cloudLibraries.google.status_message}
                  </p>
                ) : null}
                {cloudLibraries.google.stale_state_warning ? (
                  <p className="page-subnote">{cloudLibraries.google.stale_state_warning}</p>
                ) : null}
              </div>

              <form className="cloud-source-form" onSubmit={handleGoogleDriveSetupSave}>
                <label className="settings-field">
                  <span>
                    <strong>HTTPS app origin</strong>
                    <small>Use the private HTTPS hostname users actually browse to, not a raw HTTP IP address.</small>
                  </span>
                  <input
                    autoCapitalize="off"
                    autoComplete="off"
                    autoCorrect="off"
                    className="cloud-source-form__input"
                    disabled={googleDriveSetupSaving}
                    onChange={(event) =>
                      setGoogleDriveSetupDraft((current) => ({ ...current, https_origin: event.target.value }))
                    }
                    spellCheck="false"
                    type="text"
                    value={googleDriveSetupDraft.https_origin}
                  />
                </label>

                <label className="settings-field">
                  <span>
                    <strong>Google OAuth Client ID</strong>
                    <small>Paste the Web application client ID from Google Cloud.</small>
                  </span>
                  <input
                    autoCapitalize="off"
                    autoCorrect="off"
                    className="cloud-source-form__input"
                    disabled={googleDriveSetupSaving}
                    onChange={(event) =>
                      setGoogleDriveSetupDraft((current) => ({ ...current, client_id: event.target.value }))
                    }
                    spellCheck="false"
                    type="text"
                    value={googleDriveSetupDraft.client_id}
                  />
                </label>

                <label className="settings-field">
                  <span>
                    <strong>Google OAuth Client Secret</strong>
                    <small>Paste the matching client secret for this same Google OAuth app.</small>
                  </span>
                  <input
                    autoCapitalize="off"
                    autoCorrect="off"
                    className="cloud-source-form__input"
                    disabled={googleDriveSetupSaving}
                    onChange={(event) =>
                      setGoogleDriveSetupDraft((current) => ({ ...current, client_secret: event.target.value }))
                    }
                    spellCheck="false"
                    value={googleDriveSetupDraft.client_secret}
                  />
                </label>

                <div className="google-drive-callback-card">
                  <div className="google-drive-callback-card__copy">
                    <strong>Google OAuth values to register</strong>
                    <small>Google web OAuth must use this HTTPS hostname and redirect URI for this Elvern instance.</small>
                  </div>
                  <div className="google-drive-callback-card__label">Authorized JavaScript origin</div>
                  <div className="google-drive-callback-card__value">
                    {googleDriveSetup.javascript_origin || "Set a secure HTTPS app origin first."}
                  </div>
                  <div className="google-drive-callback-card__label">Authorized redirect URI</div>
                  <div className="google-drive-callback-card__value">
                    {googleDriveSetup.redirect_uri || "Available after the secure HTTPS app origin is configured."}
                  </div>
                  <div className="google-drive-callback-card__actions">
                    <button
                      className="ghost-button ghost-button--inline"
                      disabled={!googleDriveSetup.redirect_uri}
                      onClick={handleCopyGoogleDriveCallback}
                      type="button"
                    >
                      Copy redirect URI
                    </button>
                  </div>
                  {googleDriveSetup.callback_warning ? (
                    <p className="page-subnote">{googleDriveSetup.callback_warning}</p>
                  ) : null}
                </div>

                <div className="google-drive-setup-instructions">
                  <strong>Setup steps</strong>
                  <ol>
                    {googleDriveSetup.instructions.map((step, index) => (
                      <li key={`google-drive-step-${index}`}>{step}</li>
                    ))}
                  </ol>
                </div>

                <div className="player-actions">
                  <button className="primary-button" disabled={googleDriveSetupSaving} type="submit">
                    {googleDriveSetupSaving ? "Saving..." : "Save Google Drive Setup"}
                  </button>
                </div>
              </form>
            </div>
          </SettingsAccordionSection>
        ) : null}
      </div>
      ) : null}

      {activeSettingsSection === "hidden" ? (
      <div className="settings-grid">
        <section className="settings-card settings-card--wide">
          <details className="settings-disclosure">
            <summary className="settings-disclosure__summary">
              <span className="settings-disclosure__header">
                <span className="settings-disclosure__title">Hidden for me</span>
                <span className="settings-disclosure__copy">
                  This is your personal hidden list. These items stay out of your library until you restore them or move them to the global hidden list.
                </span>
              </span>
              <span className="status-pill">{hiddenItems.length}</span>
            </summary>

            <div className="settings-disclosure__body">
              {loading ? (
                <p className="page-subnote">Loading hidden movies...</p>
              ) : hiddenItems.length > 0 ? (
                <div className="hidden-movie-list">
                  {hiddenItems.map((hiddenItem) => (
                    <article className="hidden-movie-row" key={hiddenItem.id}>
                      {hiddenItem.poster_url ? (
                        <img
                          alt=""
                          className="hidden-movie-row__poster"
                          loading="lazy"
                          src={hiddenItem.poster_url}
                        />
                      ) : (
                        <div className="hidden-movie-row__poster hidden-movie-row__poster--fallback" aria-hidden="true">
                          <span>{hiddenItem.title.trim().charAt(0).toUpperCase() || "E"}</span>
                        </div>
                      )}
                      <div className="hidden-movie-row__copy">
                        <strong>{hiddenItem.title}</strong>
                        <div className="detail-list">
                          {hiddenItem.year ? <span>{hiddenItem.year}</span> : null}
                          {hiddenItem.edition_label ? <span>{hiddenItem.edition_label}</span> : null}
                        </div>
                      </div>
                      <div className="hidden-movie-row__actions">
                        <button
                          className="ghost-button ghost-button--inline"
                          disabled={restoringItemId === hiddenItem.id || movingToGlobalItemId === hiddenItem.id}
                          onClick={() => handleShowAgain(hiddenItem.id)}
                          type="button"
                        >
                          {restoringItemId === hiddenItem.id ? "Restoring..." : "Show again"}
                        </button>
                        {user?.role === "admin" ? (
                          <button
                            className="ghost-button ghost-button--inline ghost-button--danger"
                            disabled={movingToGlobalItemId === hiddenItem.id || restoringItemId === hiddenItem.id}
                            onClick={() => handleHideUniversally(hiddenItem)}
                            type="button"
                          >
                            {movingToGlobalItemId === hiddenItem.id ? "Hiding globally..." : "Hide universally"}
                          </button>
                        ) : null}
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <p className="page-subnote">You have no hidden movies right now.</p>
              )}
            </div>
          </details>
        </section>

        {user?.role === "admin" ? (
          <section className="settings-card settings-card--wide">
            <details className="settings-disclosure">
              <summary className="settings-disclosure__summary">
                <span className="settings-disclosure__header">
                  <span className="settings-disclosure__title">Hidden for everyone</span>
                  <span className="settings-disclosure__copy">
                    Admin-only restore list for movies hidden globally from regular users.
                  </span>
                </span>
                <span className="status-pill">{globalHiddenItems.length}</span>
              </summary>

              <div className="settings-disclosure__body">
                {loading ? (
                  <p className="page-subnote">Loading globally hidden movies...</p>
                ) : globalHiddenItems.length > 0 ? (
                  <div className="hidden-movie-list">
                    {globalHiddenItems.map((hiddenItem) => (
                      <article className="hidden-movie-row" key={hiddenItem.id}>
                        {hiddenItem.poster_url ? (
                          <img
                            alt=""
                            className="hidden-movie-row__poster"
                            loading="lazy"
                            src={hiddenItem.poster_url}
                          />
                        ) : (
                          <div className="hidden-movie-row__poster hidden-movie-row__poster--fallback" aria-hidden="true">
                            <span>{hiddenItem.title.trim().charAt(0).toUpperCase() || "E"}</span>
                          </div>
                        )}
                        <div className="hidden-movie-row__copy">
                          <strong>{hiddenItem.title}</strong>
                          <div className="detail-list">
                          {hiddenItem.year ? <span>{hiddenItem.year}</span> : null}
                          {hiddenItem.edition_label ? <span>{hiddenItem.edition_label}</span> : null}
                        </div>
                      </div>
                        <div className="hidden-movie-row__actions">
                          <button
                            className="ghost-button ghost-button--inline"
                            disabled={restoringGlobalItemId === hiddenItem.id || movingToPersonalItemId === hiddenItem.id}
                            onClick={() => handleShowForEveryone(hiddenItem.id)}
                            type="button"
                          >
                            {restoringGlobalItemId === hiddenItem.id ? "Restoring..." : "Show again"}
                          </button>
                          <button
                            className="ghost-button ghost-button--inline ghost-button--subtle"
                            disabled={movingToPersonalItemId === hiddenItem.id || restoringGlobalItemId === hiddenItem.id}
                            onClick={() => handleHideForMe(hiddenItem)}
                            type="button"
                          >
                            {movingToPersonalItemId === hiddenItem.id ? "Hiding for me..." : "Hide for me"}
                          </button>
                        </div>
                      </article>
                    ))}
                  </div>
                ) : (
                  <p className="page-subnote">No globally hidden movies right now.</p>
                )}
              </div>
            </details>
          </section>
        ) : null}
      </div>
      ) : null}

      {activeSettingsSection === "advanced" ? (
      <div className="settings-grid">
        {totpStatus?.setup_available && !totpStatus?.enabled ? (
          <SettingsAccordionSection
            description="Your admin has enabled two-factor setup for this account. You can finish setup here whenever you're ready."
            isOpen={openSections.totpSetup}
            onToggle={() => toggleSection("totpSetup")}
            title="Two-factor authentication"
          >
            <div className="desktop-playback-notes">
              <p className="page-subnote">
                Setup was skipped during login, but it remains available because 2FA is enabled for your account.
              </p>
              <div className="player-actions">
                <button className="primary-button" onClick={() => navigate("/setup/totp")} type="button">
                  Set up 2FA
                </button>
              </div>
            </div>
          </SettingsAccordionSection>
        ) : null}

        {user?.role === "admin" ? (
          <SettingsAccordionSection
            description="Admin-only real shared local library path. This is the live shared local library path Elvern currently uses."
            isOpen={openSections.mediaLibraryReference}
            onToggle={() => toggleSection("mediaLibraryReference")}
            title="Shared local library path"
          >
            <form className="admin-form" onSubmit={handleSharedMediaLibraryReferenceSave}>
              <label>
                Shared local library path
                <div className="settings-path-picker__row">
                  <input
                    disabled={loading || sharedMediaLibraryReferenceSaving}
                    onChange={(event) => setSharedMediaLibraryReferenceInput(event.target.value)}
                    placeholder={sharedMediaLibraryReference.default_value || ""}
                    type="text"
                    value={sharedMediaLibraryReferenceInput}
                  />
                  <button
                    aria-label="Browse shared local library directories on the Elvern host"
                    className="ghost-button ghost-button--inline settings-path-picker__button"
                    disabled={
                      loading
                      || sharedMediaLibraryReferenceSaving
                      || (directoryPicker.loading && directoryPicker.target === "shared-library")
                      || nativePickerPendingTarget === "shared-library"
                    }
                    onClick={() => handleOpenDirectoryPicker("shared-library")}
                    title="Browse shared local library directories on the Elvern host"
                    type="button"
                  >
                    <span aria-hidden="true">📁</span>
                  </button>
                </div>
              </label>
              <StatusRow label="Current path" value={sharedMediaLibraryReference.effective_value || "Unknown"} />
              <StatusRow label="Default path" value={sharedMediaLibraryReference.default_value || "Unknown"} />
              <div className="desktop-playback-notes">
                {(sharedMediaLibraryReference.validation_rules || []).map((rule) => (
                  <p className="page-subnote" key={rule}>
                    {rule}
                  </p>
                ))}
              </div>
              {nativePickerPendingTarget === "shared-library" ? (
                <div className="desktop-playback-notes">
                  <p className="page-note">Opening folder picker…</p>
                </div>
              ) : null}
              {directoryPickerFallback.target === "shared-library" && directoryPickerFallback.reason ? (
                <div className="desktop-playback-notes">
                  <p className="form-error">{directoryPickerFallback.reason}</p>
                  <div className="player-actions">
                    <button
                      className="ghost-button"
                      onClick={() => handleOpenServerDirectoryBrowser("shared-library")}
                      type="button"
                    >
                      Browse server directories instead
                    </button>
                  </div>
                </div>
              ) : null}
              <div className="player-actions">
                <button
                  className="primary-button"
                  disabled={loading || sharedMediaLibraryReferenceSaving}
                  type="submit"
                >
                  {sharedMediaLibraryReferenceSaving ? "Saving..." : "Save shared local library path"}
                </button>
              </div>
            </form>
          </SettingsAccordionSection>
        ) : null}

        {user?.role === "admin" ? (
          <SettingsAccordionSection
            description="Global admin-only poster directory for every user. Leave this at the current Linux default unless you need Elvern to scan a different mounted poster folder."
            isOpen={openSections.posterReference}
            onToggle={() => toggleSection("posterReference")}
            title="Poster reference location"
          >
            <form className="admin-form" onSubmit={handlePosterReferenceSave}>
              <label>
                Poster directory
                <div className="settings-path-picker__row">
                  <input
                    autoCapitalize="off"
                    autoCorrect="off"
                    disabled={loading || posterReferenceSaving}
                    onChange={(event) => setPosterReferenceInput(event.target.value)}
                    placeholder={posterReference.default_value || ""}
                    spellCheck="false"
                    type="text"
                    value={posterReferenceInput}
                  />
                  <button
                    aria-label="Browse poster directories on the Elvern host"
                    className="ghost-button ghost-button--inline settings-path-picker__button"
                    disabled={
                      loading
                      || posterReferenceSaving
                      || (directoryPicker.loading && directoryPicker.target === "poster-reference")
                      || nativePickerPendingTarget === "poster-reference"
                    }
                    onClick={() => handleOpenDirectoryPicker("poster-reference")}
                    title="Browse poster directories on the Elvern host"
                    type="button"
                  >
                    <span aria-hidden="true">📁</span>
                  </button>
                </div>
              </label>
              <StatusRow label="Current path" value={posterReference.effective_value || "Unknown"} />
              <StatusRow label="Default path" value={posterReference.default_value || "Unknown"} />
              <div className="desktop-playback-notes">
                {(posterReference.validation_rules || []).map((rule) => (
                  <p className="page-subnote" key={rule}>
                    {rule}
                  </p>
                ))}
              </div>
              {nativePickerPendingTarget === "poster-reference" ? (
                <div className="desktop-playback-notes">
                  <p className="page-note">Opening folder picker…</p>
                </div>
              ) : null}
              {directoryPickerFallback.target === "poster-reference" && directoryPickerFallback.reason ? (
                <div className="desktop-playback-notes">
                  <p className="form-error">{directoryPickerFallback.reason}</p>
                  <div className="player-actions">
                    <button
                      className="ghost-button"
                      onClick={() => handleOpenServerDirectoryBrowser("poster-reference")}
                      type="button"
                    >
                      Browse server directories instead
                    </button>
                  </div>
                </div>
              ) : null}
              <div className="player-actions">
                <button
                  className="primary-button"
                  disabled={loading || posterReferenceSaving}
                  type="submit"
                >
                  {posterReferenceSaving ? "Saving..." : "Save poster location"}
                </button>
              </div>
            </form>
          </SettingsAccordionSection>
        ) : null}

      </div>
      ) : null}
    </section>
  );
}
