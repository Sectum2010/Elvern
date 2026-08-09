import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import {
  ApiResponseError,
  apiRequest,
  isAbortError,
  isTransientNetworkError,
} from "../lib/api";
import {
  formatGoogleConnectionHealthLabel,
  formatGoogleDriveSetupLabel,
} from "../lib/cloudSyncStatus";
import { startGoogleDriveReconnect } from "../lib/providerAuth";
import { invalidateLibraryQueries } from "../lib/libraryQueries";
import {
  BACKGROUND_PRESETS,
  DEFAULT_BACKGROUND_SETTINGS,
  buildBackgroundPreviewStyle,
  deriveGradientColorsFromSingleColor,
  deriveGradientEndFromSingleColor,
  getBackgroundPickerColorAtPosition,
  getBackgroundPickerPositionFromColor,
  hueToHex,
  normalizeUserBackgroundSettings,
} from "../lib/userBackground";
import { NonLoginSecretInput } from "../components/NonLoginSecretInput";
import { RefreshSweepButton } from "../components/RefreshSweepButton";
import { DesktopBackToLibraryButton } from "../components/DesktopBackToLibraryButton";
import { InstallSettingsPanel } from "../features/install/InstallSettingsPanel";
import { MeridianSettingsView } from "../components/meridian/MeridianSettingsView.jsx";
import {
  CONNECTIVITY_RECOVERED_EVENT,
  getConnectivityIncidentRecoveryGeneration,
} from "../lib/connectivityRecoveryStore";
import { normalizePosterDisplayWidth } from "../lib/posterUrls";
import {
  applySettingsSectionStorageMigration,
  buildSettingsSectionLocation,
  resolveSettingsSection,
  writePersistedSettingsSection,
} from "../lib/settingsSectionState";
import { detectClientDeviceClass, detectClientPlatform } from "../lib/platformDetection";
import {
  resolveUserSettings,
  setUserSettingsQueryData,
  useUserSettingsQuery,
} from "../lib/userSettingsQueries";
import {
  classifyControlCenterPath,
  desktopSettingsTabToLegacySection,
} from "../lib/controlCenterRoutes.js";
import {
  fetchControlCenterResource,
  setControlCenterResourceData,
} from "../lib/controlCenterQueries.js";

const USER_SETTINGS_CHANGED_EVENT = "elvern:user-settings-changed";
const SETTINGS_RESOURCE_KEYS = Object.freeze([
  "hidden",
  "cloud",
  "ageGroups",
  "googleSetup",
  "mediaReference",
  "posterReference",
  "totp",
]);

const SETTINGS_SECTIONS = [
  { key: "preferences", label: "Preferences", icon: "preferences" },
  { key: "libraries", label: "Libraries", icon: "libraries" },
  { key: "install", label: "Install", icon: "install" },
  { key: "advanced", label: "Advanced", icon: "advanced" },
];
const EMPTY_RESOURCE_STATUS = Object.freeze({
  loading: false,
  loaded: false,
  error: "",
});
const HIDDEN_RECONCILIATION_MAX_AGE_MS = 2 * 60 * 1000;


function sanitizeGoogleDriveSetupPayload(payload) {
  const { client_secret: _clientSecret, ...safePayload } = payload || {};
  return safePayload;
}


function createSettingsResourceStatus() {
  return Object.fromEntries(
    SETTINGS_RESOURCE_KEYS.map((key) => [key, { ...EMPTY_RESOURCE_STATUS }]),
  );
}


function settingsResourcesForSection(section, role, desktopTab = "") {
  if (desktopTab) {
    if (desktopTab === "library") {
      return ["totp", ...(role === "admin" ? ["ageGroups"] : [])];
    }
    if (desktopTab === "cloud-sharing") {
      return ["totp", "cloud"];
    }
    if (desktopTab === "hidden-titles") {
      return ["totp", "hidden"];
    }
    if (desktopTab === "server-storage" && role === "admin") {
      return ["totp", "googleSetup", "cloud", "mediaReference", "posterReference"];
    }
    return ["totp"];
  }
  if (section === "libraries") {
    return [
      "hidden",
      "cloud",
      ...(role === "admin" ? ["ageGroups"] : []),
    ];
  }
  if (section === "advanced") {
    return [
      "totp",
      ...(role === "admin"
        ? ["googleSetup", "cloud", "mediaReference", "posterReference"]
        : []),
    ];
  }
  return [];
}
const AGE_REQUIREMENT_OPTIONS = [null, ...Array.from({ length: 18 }, (_, index) => index + 1)];

const POSTER_CARD_APPEARANCE_OPTIONS = [
  { value: "classic", label: "Classic" },
  { value: "modern", label: "Modern" },
  { value: "clean", label: "Clean" },
];

const POSTER_DISPLAY_WIDTH_OPTIONS = [
  { value: "800", label: "800 px" },
  { value: "1000", label: "1000 px" },
  { value: "1400", label: "1400 px" },
  { value: "original", label: "Original" },
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


function buildRestrictedAgeBuckets(items = []) {
  const buckets = new Map();
  for (const group of items || []) {
    const age = Number(group?.age_requirement);
    if (!Number.isInteger(age) || age < 1) {
      continue;
    }
    const bucket = buckets.get(age) || {
      age,
      ageLabel: formatAgeRequirement(age),
      copiesCount: 0,
      groupCount: 0,
      manualLinksCount: 0,
      groups: [],
    };
    bucket.groupCount += 1;
    bucket.copiesCount += Number(group?.copies_count) || 0;
    bucket.manualLinksCount += Number(group?.manual_links_count) || 0;
    bucket.groups.push(group);
    buckets.set(age, bucket);
  }
  return Array.from(buckets.values()).sort((left, right) => left.age - right.age);
}


function formatCountLabel(count, singular, plural = `${singular}s`) {
  const value = Number(count) || 0;
  return `${value} ${value === 1 ? singular : plural}`;
}


function hiddenItemsShareIdentity(left, right) {
  const leftId = Number(left?.id);
  const rightId = Number(right?.id);
  return Number.isInteger(leftId) && leftId > 0 && leftId === rightId;
}


function isUncertainHiddenScopeError(error) {
  return (
    isTransientNetworkError(error)
    || (
      error instanceof ApiResponseError
      && Number(error.status) >= 200
      && Number(error.status) < 300
    )
  );
}


function hiddenScopeReached(lists, itemId, targetScope) {
  const identity = { id: itemId };
  const inPersonal = lists.personalItems.some((item) => hiddenItemsShareIdentity(item, identity));
  const inGlobal = lists.globalItems.some((item) => hiddenItemsShareIdentity(item, identity));
  return targetScope === "global"
    ? inGlobal && !inPersonal
    : inPersonal && !inGlobal;
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
  if (icon === "install") {
    return (
      <svg aria-hidden="true" className="admin-nav-card__icon-svg" viewBox="0 0 24 24">
        <path d="M12 4v9M8.5 9.5 12 13l3.5-3.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
        <path d="M5 14.5v2.2A2.3 2.3 0 0 0 7.3 19h9.4a2.3 2.3 0 0 0 2.3-2.3v-2.2" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
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


const LIBRARY_REFERENCE_SUMMARY_CATEGORIES = [
  { key: "movies", label: "Movies stored under" },
  { key: "tv", label: "TV stored under" },
  { key: "cartoon", label: "Cartoon stored under" },
  { key: "anime", label: "Anime stored under" },
];


function firstNonEmptyLine(value) {
  return String(value || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean) || "";
}

function appendUniqueReferenceLocation(value, nextPath) {
  const existing = String(value || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const candidate = String(nextPath || "").trim();
  if (!candidate || existing.includes(candidate)) {
    return existing.join("\n");
  }
  return [...existing, candidate].join("\n");
}


function LibraryReferenceCategorySummary({ summary }) {
  const safeSummary = summary && typeof summary === "object" ? summary : {};
  return (
    <div className="settings-library-reference-summary">
      {LIBRARY_REFERENCE_SUMMARY_CATEGORIES.map((category) => {
        const locations = Array.isArray(safeSummary[category.key]) ? safeSummary[category.key] : [];
        return (
          <div className="settings-library-reference-summary__row" key={category.key}>
            <span>{category.label}:</span>
            <strong>
              {locations.length > 0 ? (
                locations.map((location) => (
                  <span className="settings-library-reference-summary__path" key={location.path || location.name}>
                    {location.path || location.name || "Unknown"}
                  </span>
                ))
              ) : (
                "Unknown"
              )}
            </strong>
          </div>
        );
      })}
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


function isSettingsLocalDevelopmentLoopback(platform) {
  if (typeof window === "undefined" || platform !== "linux") {
    return false;
  }
  const host = (window.location.hostname || "").toLowerCase();
  return host === "localhost" || host === "127.0.0.1";
}


function directoryPickerPurposeForTarget(target) {
  if (target === "poster-reference") {
    return "poster_reference";
  }
  if (target === "shared-library") {
    return "library_reference";
  }
  return "generic";
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


function SettingsAccordionSection({ title, description, badge, isOpen, onToggle, children, className = "" }) {
  return (
    <section className={`settings-card settings-card--wide ${className}`.trim()}>
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


function AgeBucketManagerModal({
  bucket,
  error,
  onClose,
  onManageGroup,
  onRemoveRequirement,
  open,
  savingKey,
}) {
  if (!open) {
    return null;
  }

  const groups = bucket?.groups || [];
  const title = bucket ? `Age ${bucket.ageLabel} groups` : "Age groups";

  return (
    <div
      aria-labelledby="settings-age-bucket-title"
      aria-modal="true"
      className="browser-resume-modal"
      role="dialog"
    >
      <div aria-hidden="true" className="browser-resume-modal__backdrop" onClick={onClose} />
      <div className="browser-resume-modal__card settings-age-group-modal">
        <div className="detail-info-modal__header">
          <div className="detail-info-modal__copy">
            <p className="eyebrow detail-info-modal__eyebrow">Admin</p>
            <h2 className="detail-info-modal__title" id="settings-age-bucket-title">{title}</h2>
          </div>
          <button className="ghost-button ghost-button--inline detail-info-modal__close" onClick={onClose} type="button">
            Close
          </button>
        </div>

        <div className="detail-info-modal__body settings-age-group-modal__body">
          {error ? <p className="form-error">{error}</p> : null}
          {groups.length > 0 ? (
            <div className="settings-age-bucket-groups">
              {groups.map((group) => (
                <article className="settings-age-bucket-group" key={`age-bucket-${group.age_group_key}`}>
                  <div className="settings-age-group-row__copy">
                    <strong>{group.display_title}</strong>
                    <small>
                      {group.year || "Year unknown"} · {group.copies_count} copies
                      {group.manual_links_count ? ` · ${group.manual_links_count} manual` : ""}
                    </small>
                  </div>
                  <div className="settings-age-bucket-group__actions">
                    <button
                      className="ghost-button ghost-button--inline"
                      onClick={() => onManageGroup(group)}
                      type="button"
                    >
                      Manage group
                    </button>
                    <button
                      className="ghost-button ghost-button--inline ghost-button--danger"
                      disabled={savingKey === group.age_group_key || !group.primary_media_item_id}
                      onClick={() => onRemoveRequirement(group)}
                      type="button"
                    >
                      {savingKey === group.age_group_key ? "Removing..." : "Remove age requirement"}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <p className="page-subnote">No groups remain in this age bucket.</p>
          )}
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
  const userSettingsQuery = useUserSettingsQuery(user);
  const settingsIdentity = `${String(user?.id ?? "")}:${String(user?.role || "").trim().toLowerCase()}`;
  const controlCenterPath = classifyControlCenterPath(location.pathname);
  const settingsSectionResolution = controlCenterPath.area === "settings" && controlCenterPath.tab
    ? {
        section: desktopSettingsTabToLegacySection(controlCenterPath.tab, user?.role),
        shouldReplace: false,
        needsStorageMigration: false,
        storageMigrationTarget: null,
      }
    : resolveSettingsSection({ search: location.search, hash: location.hash });
  const settingsHydratedIdentityRef = useRef("");
  const previousSettingsIdentityRef = useRef(settingsIdentity);
  const mountedRef = useRef(true);
  const currentIdentityRef = useRef(settingsIdentity);
  const resourceRequestsRef = useRef(new Map());
  const resourceRequestTokenRef = useRef(0);
  const hiddenReadOperationRef = useRef(null);
  const hiddenReadOperationTokenRef = useRef(0);
  const pendingHiddenReconciliationRef = useRef(null);
  const pendingHiddenReconciliationTokenRef = useRef(0);
  const pendingHiddenExpiryTimerRef = useRef(null);
  const pendingReconcileHandlerRef = useRef(null);
  const hiddenHashRestoreKeyRef = useRef("");
  const oauthHashRestoreKeyRef = useRef("");
  const [settings, setSettings] = useState({
    hide_duplicate_movies: true,
    hide_recently_added: false,
    floating_library_search_enabled: true,
    desktop_floating_island_position: "top",
    poster_card_appearance: "classic",
    poster_card_display_max_width: "1400",
    ...DEFAULT_BACKGROUND_SETTINGS,
    media_library_reference_private_value: null,
    media_library_reference_shared_default_value: "",
    media_library_reference_effective_value: "",
    media_library_reference_effective_source: "shared_default",
    media_library_reference_effective_label: "Shared default",
  });
  const [backgroundDraft, setBackgroundDraft] = useState(DEFAULT_BACKGROUND_SETTINGS);
  const [backgroundSaving, setBackgroundSaving] = useState(false);
  const [backgroundError, setBackgroundError] = useState("");
  const [backgroundResetConfirmOpen, setBackgroundResetConfirmOpen] = useState(false);
  const [activeSettingsSection, setActiveSettingsSection] = useState(
    () => settingsSectionResolution.section,
  );
  const [activeSettingsButtonExpanded, setActiveSettingsButtonExpanded] = useState(true);
  const [hiddenItems, setHiddenItems] = useState([]);
  const [globalHiddenItems, setGlobalHiddenItems] = useState([]);
  const [hiddenListsExpanded, setHiddenListsExpanded] = useState({ personal: false, global: false });
  const [resourceStatus, setResourceStatus] = useState(createSettingsResourceStatus);
  const [pendingHiddenReconciliation, setPendingHiddenReconciliation] = useState(null);
  const [saving, setSaving] = useState(false);
  const [restoringItemId, setRestoringItemId] = useState(null);
  const [restoringGlobalItemId, setRestoringGlobalItemId] = useState(null);
  const [movingToGlobalItemId, setMovingToGlobalItemId] = useState(null);
  const [movingToPersonalItemId, setMovingToPersonalItemId] = useState(null);
  const [sharedMediaLibraryReference, setSharedMediaLibraryReference] = useState({
    configured_value: null,
    effective_value: "",
    default_value: "",
    configured_locations: [],
    effective_locations: [],
    category_summary: {},
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
  const [ageGroupsPanelOpen, setAgeGroupsPanelOpen] = useState(false);
  const [ageBucketManager, setAgeBucketManager] = useState({
    open: false,
    age: null,
    savingKey: "",
    error: "",
  });
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
  const [googleDestructiveAction, setGoogleDestructiveAction] = useState("");
  const [totpStatus, setTotpStatus] = useState({
    enabled: false,
    setup_available: false,
  });
  const [userSettingsLoadError, setUserSettingsLoadError] = useState("");
  const [mutationError, setMutationError] = useState("");
  const error = mutationError;
  const setError = setMutationError;
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
  const restrictedAgeBuckets = buildRestrictedAgeBuckets(ageGroups.items || []);
  const activeAgeBucket = ageBucketManager.open
    ? restrictedAgeBuckets.find((bucket) => bucket.age === ageBucketManager.age)
    : null;
  const userSettingsLoading = !userSettingsQuery.data && userSettingsQuery.isPending;
  const loading = userSettingsLoading;
  const hiddenLoading = resourceStatus.hidden.loading;
  const ageGroupsLoading = resourceStatus.ageGroups.loading;
  const settingsClientDeviceClass = detectClientDeviceClass();
  const settingsClientPlatform = detectClientPlatform();
  const showDesktopFloatingIslandSettings = settingsClientDeviceClass === "desktop"
    && ["windows", "mac", "linux"].includes(settingsClientPlatform);
  const showDesktopControlCenter = showDesktopFloatingIslandSettings
    && controlCenterPath.area === "settings"
    && Boolean(controlCenterPath.tab);
  const desktopSettingsTab = showDesktopControlCenter
    ? controlCenterPath.tab
    : "";
  const visiblePersonalHiddenItems = showDesktopControlCenter && !hiddenListsExpanded.personal
    ? hiddenItems.slice(0, 4)
    : hiddenItems;
  const visibleGlobalHiddenItems = showDesktopControlCenter && !hiddenListsExpanded.global
    ? globalHiddenItems.slice(0, 4)
    : globalHiddenItems;
  const activeResourceKeys = settingsResourcesForSection(
    activeSettingsSection,
    user?.role,
    desktopSettingsTab,
  );
  const activeResourceErrors = Object.entries(resourceStatus)
    .filter(([key]) => activeResourceKeys.includes(key))
    .filter(([, status]) => status.error)
    .map(([key, status]) => ({ key, message: status.error }));

  currentIdentityRef.current = settingsIdentity;

  const clearPendingHiddenExpiryTimer = useCallback(() => {
    if (pendingHiddenExpiryTimerRef.current !== null) {
      window.clearTimeout(pendingHiddenExpiryTimerRef.current);
      pendingHiddenExpiryTimerRef.current = null;
    }
  }, []);

  const clearPendingHiddenReconciliation = useCallback(() => {
    clearPendingHiddenExpiryTimer();
    pendingHiddenReconciliationRef.current = null;
    setPendingHiddenReconciliation(null);
  }, [clearPendingHiddenExpiryTimer]);

  const schedulePendingHiddenExpiry = useCallback((pending) => {
    clearPendingHiddenExpiryTimer();
    const delay = Math.max(Number(pending.expiresAt) - Date.now(), 0);
    pendingHiddenExpiryTimerRef.current = window.setTimeout(() => {
      pendingHiddenExpiryTimerRef.current = null;
      if (
        !mountedRef.current
        || currentIdentityRef.current !== pending.identity
        || pendingHiddenReconciliationRef.current !== pending
        || pendingHiddenReconciliationTokenRef.current !== pending.token
        || pending.expired
      ) {
        return;
      }
      pending.expired = true;
      setPendingHiddenReconciliation({ ...pending });
    }, delay);
  }, [clearPendingHiddenExpiryTimer]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      hiddenReadOperationRef.current?.controller?.abort();
      hiddenReadOperationRef.current = null;
      clearPendingHiddenExpiryTimer();
      for (const request of resourceRequestsRef.current.values()) {
        request.controller?.abort();
      }
      resourceRequestsRef.current.clear();
      pendingHiddenReconciliationRef.current = null;
    };
  }, [clearPendingHiddenExpiryTimer]);

  useLayoutEffect(() => {
    if (previousSettingsIdentityRef.current === settingsIdentity) {
      return;
    }
    previousSettingsIdentityRef.current = settingsIdentity;
    for (const request of resourceRequestsRef.current.values()) {
      request.controller?.abort();
    }
    hiddenReadOperationRef.current?.controller?.abort();
    hiddenReadOperationRef.current = null;
    clearPendingHiddenExpiryTimer();
    resourceRequestsRef.current.clear();
    pendingHiddenReconciliationRef.current = null;
    settingsHydratedIdentityRef.current = "";
    hiddenHashRestoreKeyRef.current = "";
    oauthHashRestoreKeyRef.current = "";
    setPendingHiddenReconciliation(null);
    setResourceStatus(createSettingsResourceStatus());
    setSettings({
      hide_duplicate_movies: true,
      hide_recently_added: false,
      floating_library_search_enabled: true,
      desktop_floating_island_position: "top",
      poster_card_appearance: "classic",
      poster_card_display_max_width: "1400",
      ...DEFAULT_BACKGROUND_SETTINGS,
      media_library_reference_private_value: null,
      media_library_reference_shared_default_value: "",
      media_library_reference_effective_value: "",
      media_library_reference_effective_source: "shared_default",
      media_library_reference_effective_label: "Shared default",
    });
    setBackgroundDraft(DEFAULT_BACKGROUND_SETTINGS);
    setBackgroundSaving(false);
    setBackgroundError("");
    setBackgroundResetConfirmOpen(false);
    setHiddenItems([]);
    setGlobalHiddenItems([]);
    setHiddenListsExpanded({ personal: false, global: false });
    setSaving(false);
    setRestoringItemId(null);
    setRestoringGlobalItemId(null);
    setMovingToGlobalItemId(null);
    setMovingToPersonalItemId(null);
    setSharedMediaLibraryReference({
      configured_value: null,
      effective_value: "",
      default_value: "",
      configured_locations: [],
      effective_locations: [],
      category_summary: {},
      validation_rules: [],
    });
    setSharedMediaLibraryReferenceInput("");
    setSharedMediaLibraryReferenceSaving(false);
    setPosterReference({
      configured_value: null,
      effective_value: "",
      default_value: "",
      validation_rules: [],
    });
    setPosterReferenceInput("");
    setPosterReferenceSaving(false);
    setCloudLibraries({
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
    setCloudBusyKey("");
    setAgeGroups({ items: [], total: 0 });
    setAgeGroupsPanelOpen(false);
    setAgeBucketManager({
      open: false,
      age: null,
      savingKey: "",
      error: "",
    });
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
    setMyLibraryDraft({ resource_type: "folder", resource_id: "" });
    setSharedLibraryDraft({ resource_type: "folder", resource_id: "" });
    setGoogleDriveSetup({
      https_origin: "",
      client_id: "",
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
    setGoogleDriveSetupDraft({ https_origin: "", client_id: "", client_secret: "" });
    setGoogleDriveSetupSaving(false);
    setGoogleDestructiveAction("");
    setTotpStatus({ enabled: false, setup_available: false });
    setOpenSections({
      myLibraries: false,
      sharedLibraries: false,
      googleDriveSetup: false,
      mediaLibraryReference: false,
      posterReference: false,
      totpSetup: false,
    });
    setDirectoryPicker({
      open: false,
      target: "shared-library",
      title: "",
      loading: false,
      error: "",
      current_path: "",
      parent_path: null,
      directories: [],
    });
    setDirectoryPickerFallback({ target: "", reason: "" });
    setNativePickerPendingTarget("");
    setUserSettingsLoadError("");
    setError("");
    setMessage("");
  }, [clearPendingHiddenExpiryTimer, settingsIdentity]);

  useEffect(() => {
    if (!userSettingsQuery.data) {
      return;
    }
    const resolvedSettings = resolveUserSettings(userSettingsQuery.data);
    setSettings(resolvedSettings);
    if (settingsHydratedIdentityRef.current !== settingsIdentity) {
      settingsHydratedIdentityRef.current = settingsIdentity;
      setBackgroundDraft(normalizeUserBackgroundSettings(resolvedSettings));
    }
  }, [settingsIdentity, userSettingsQuery.data]);

  useEffect(() => {
    if (userSettingsQuery.error && userSettingsQuery.error.name !== "AbortError") {
      setUserSettingsLoadError(
        userSettingsQuery.error.message || "Failed to load settings",
      );
      return;
    }
    if (userSettingsQuery.data) {
      setUserSettingsLoadError("");
    }
  }, [settingsIdentity, userSettingsQuery.data, userSettingsQuery.error]);

  const fetchHiddenLists = useCallback(async ({ signal, force = false } = {}) => {
    const sharedQueryOptions = {
      userId: user?.id,
      role: user?.role,
      force,
    };
    const useSharedControlCenterCache = showDesktopControlCenter && user?.role === "admin";
    const [personalPayload, globalPayload] = await Promise.all([
      useSharedControlCenterCache
        ? fetchControlCenterResource({ ...sharedQueryOptions, resource: "personalHidden" })
        : apiRequest("/api/user-hidden-items", { signal }),
      user?.role === "admin"
        ? (useSharedControlCenterCache
          ? fetchControlCenterResource({ ...sharedQueryOptions, resource: "globalHidden" })
          : apiRequest("/api/admin/global-hidden-items", { signal }))
        : Promise.resolve({ items: [] }),
    ]);
    return {
      personalItems: personalPayload.items || [],
      globalItems: globalPayload.items || [],
    };
  }, [showDesktopControlCenter, user?.id, user?.role]);

  const refreshHiddenLists = useCallback(async ({
    signal,
    ownerKind = "mutation_refresh",
  } = {}) => {
    const expectedIdentity = settingsIdentity;
    const operationToken = hiddenReadOperationTokenRef.current + 1;
    hiddenReadOperationTokenRef.current = operationToken;
    hiddenReadOperationRef.current?.controller?.abort();
    const controller = new AbortController();
    const operation = {
      operationToken,
      generation: operationToken,
      identity: expectedIdentity,
      ownerKind,
      controller,
      startedAt: Date.now(),
    };
    hiddenReadOperationRef.current = operation;
    const abortFromParent = () => controller.abort();
    if (signal?.aborted) {
      controller.abort();
    } else {
      signal?.addEventListener("abort", abortFromParent, { once: true });
    }

    const isCurrentOwner = () => (
      mountedRef.current
      && currentIdentityRef.current === expectedIdentity
      && hiddenReadOperationRef.current === operation
    );
    try {
      const lists = await fetchHiddenLists({
        signal: controller.signal,
        force: ownerKind !== "resource_load",
      });
      if (!isCurrentOwner()) {
        return { outcome: "superseded", lists: null, error: null };
      }
      setHiddenItems(lists.personalItems);
      setGlobalHiddenItems(lists.globalItems);
      const request = resourceRequestsRef.current.get("hidden");
      if (request?.identity === expectedIdentity) {
        request.loaded = true;
        request.transient = false;
        request.failureId = 0;
        request.incidentId = 0;
      }
      setResourceStatus((current) => ({
        ...current,
        hidden: {
          loading: Boolean(request?.loading),
          loaded: true,
          error: "",
        },
      }));
      return { outcome: "applied", lists, error: null };
    } catch (requestError) {
      if (!isCurrentOwner()) {
        return { outcome: "superseded", lists: null, error: null };
      }
      if (isAbortError(requestError)) {
        return { outcome: "aborted", lists: null, error: requestError };
      }
      return { outcome: "failed", lists: null, error: requestError };
    } finally {
      signal?.removeEventListener("abort", abortFromParent);
    }
  }, [fetchHiddenLists, settingsIdentity]);

  const reconcilePendingHiddenScope = useCallback(async (
    pending = pendingHiddenReconciliationRef.current,
    { manual = false } = {},
  ) => {
    if (!pending || pendingHiddenReconciliationRef.current !== pending) {
      return false;
    }
    const expired = Date.now() >= pending.expiresAt;
    if (expired && !manual) {
      pending.expired = true;
      setPendingHiddenReconciliation({ ...pending });
      return false;
    }
    if (pending.reconciling) {
      return false;
    }
    pending.reconciling = true;
    try {
      const result = await refreshHiddenLists({
        ownerKind: manual ? "manual_retry" : "reconciliation",
      });
      if (
        result.outcome !== "applied"
        || pendingHiddenReconciliationRef.current !== pending
      ) {
        if (result.outcome === "failed") {
          const requestError = result.error;
          const hasExpired = Date.now() >= pending.expiresAt;
          if (hasExpired) {
            pending.expired = true;
            setPendingHiddenReconciliation({ ...pending });
          }
          if (isTransientNetworkError(requestError)) {
            pending.incidentId = Number(requestError.incidentId) || pending.incidentId || 0;
            pending.failureId = Number(requestError.failureId) || pending.failureId || 0;
            const recoveredGeneration = getConnectivityIncidentRecoveryGeneration(
              pending.incidentId,
              pending.failureId,
            );
            if (
              !hasExpired
              && recoveredGeneration > Number(pending.lastRecoveryGeneration || 0)
            ) {
              pending.lastRecoveryGeneration = recoveredGeneration;
              queueMicrotask(() => {
                if (
                  mountedRef.current
                  && currentIdentityRef.current === pending.identity
                  && pendingHiddenReconciliationRef.current === pending
                ) {
                  void pendingReconcileHandlerRef.current?.(pending);
                }
              });
            }
          }
        }
        return false;
      }
      const lists = result.lists;
      if (hiddenScopeReached(lists, pending.itemId, pending.requestedScope)) {
        const successMessage = pending.requestedScope === "global"
          ? "This movie is hidden for everyone."
          : "This movie is now hidden only for your account.";
        clearPendingHiddenReconciliation();
        setError("");
        setMessage(successMessage);
        void invalidateLibraryQueries();
        return true;
      }
      if (hiddenScopeReached(lists, pending.itemId, pending.sourceScope)) {
        clearPendingHiddenReconciliation();
        setMessage("");
        setError(pending.errorMessage || "The scope change was not saved.");
        return true;
      }
      return false;
    } finally {
      if (pendingHiddenReconciliationRef.current === pending) {
        pending.reconciling = false;
      }
    }
  }, [clearPendingHiddenReconciliation, refreshHiddenLists]);

  pendingReconcileHandlerRef.current = reconcilePendingHiddenScope;

  const loadSettingsResource = useCallback(async (
    resourceKey,
    { force = false, recoveryGeneration = 0 } = {},
  ) => {
    const existing = resourceRequestsRef.current.get(resourceKey);
    if (
      !force
      && existing?.identity === settingsIdentity
      && (existing.loaded || existing.loading)
    ) {
      return;
    }
    existing?.controller?.abort();
    const controller = new AbortController();
    const generation = Number(existing?.generation || 0) + 1;
    const requestToken = resourceRequestTokenRef.current + 1;
    resourceRequestTokenRef.current = requestToken;
    const requestState = {
      identity: settingsIdentity,
      requestToken,
      controller,
      generation,
      loaded: Boolean(existing?.loaded),
      loading: true,
      transient: false,
      failureId: 0,
      incidentId: 0,
      lastRecoveryGeneration: Math.max(
        Number(existing?.lastRecoveryGeneration || 0),
        Number(recoveryGeneration || 0),
      ),
    };
    resourceRequestsRef.current.set(resourceKey, requestState);
    setResourceStatus((current) => ({
      ...current,
      [resourceKey]: { loading: true, loaded: Boolean(existing?.loaded), error: "" },
    }));

    try {
      let result;
      if (resourceKey === "hidden") {
        result = await refreshHiddenLists({
          signal: controller.signal,
          ownerKind: "resource_load",
        });
        if (result.outcome === "failed") {
          throw result.error;
        }
      } else if (resourceKey === "cloud") {
        result = {
          outcome: "applied",
          payload: showDesktopControlCenter && user?.role === "admin"
            ? await fetchControlCenterResource({
              userId: user?.id,
              role: user?.role,
              resource: "cloudLibraries",
              force,
            })
            : await apiRequest("/api/cloud-libraries", { signal: controller.signal }),
        };
      } else if (resourceKey === "ageGroups") {
        result = {
          outcome: "applied",
          payload: await apiRequest("/api/library/age-groups", { signal: controller.signal }),
        };
      } else if (resourceKey === "googleSetup") {
        result = {
          outcome: "applied",
          payload: showDesktopControlCenter && user?.role === "admin"
            ? await fetchControlCenterResource({
              userId: user?.id,
              role: user?.role,
              resource: "googleDriveSetup",
              force,
            })
            : await apiRequest("/api/admin/google-drive-setup", { signal: controller.signal }),
        };
      } else if (resourceKey === "mediaReference") {
        result = {
          outcome: "applied",
          payload: await apiRequest("/api/admin/media-library-reference", { signal: controller.signal }),
        };
      } else if (resourceKey === "posterReference") {
        result = {
          outcome: "applied",
          payload: await apiRequest("/api/admin/poster-reference-location", { signal: controller.signal }),
        };
      } else if (resourceKey === "totp") {
        result = {
          outcome: "applied",
          payload: await apiRequest("/api/auth/totp/status", { signal: controller.signal }),
        };
      } else {
        return;
      }
      const current = resourceRequestsRef.current.get(resourceKey);
      if (
        !mountedRef.current
        || currentIdentityRef.current !== settingsIdentity
        || current?.requestToken !== requestToken
      ) {
        return;
      }
      if (result.outcome !== "applied") {
        return;
      }
      const payload = resourceKey === "hidden" ? result.lists : result.payload;
      if (resourceKey === "cloud") {
        setCloudLibraries(payload);
      } else if (resourceKey === "ageGroups") {
        setAgeGroups(payload || { items: [], total: 0 });
      } else if (resourceKey === "googleSetup") {
        const safePayload = sanitizeGoogleDriveSetupPayload(payload);
        setGoogleDriveSetup(safePayload);
        setGoogleDriveSetupDraft({
          https_origin: safePayload.https_origin || "",
          client_id: safePayload.client_id || "",
          client_secret: "",
        });
      } else if (resourceKey === "mediaReference") {
        setSharedMediaLibraryReference(payload);
        setSharedMediaLibraryReferenceInput(payload.configured_value || payload.default_value || "");
      } else if (resourceKey === "posterReference") {
        setPosterReference(payload);
        setPosterReferenceInput(payload.configured_value || payload.default_value || "");
      } else if (resourceKey === "totp") {
        setTotpStatus(payload);
      }
      resourceRequestsRef.current.set(resourceKey, {
        ...current,
        controller: null,
        loaded: true,
        loading: true,
        transient: false,
        failureId: 0,
        incidentId: 0,
      });
      setResourceStatus((status) => ({
        ...status,
        [resourceKey]: { loading: true, loaded: true, error: "" },
      }));
    } catch (requestError) {
      if (
        isAbortError(requestError)
        || !mountedRef.current
        || currentIdentityRef.current !== settingsIdentity
        || resourceRequestsRef.current.get(resourceKey)?.requestToken !== requestToken
      ) {
        return;
      }
      const transient = isTransientNetworkError(requestError);
      const messageText = requestError.message || "Failed to load this settings section";
      const failedRequest = {
        ...requestState,
        controller: null,
        loading: true,
        transient,
        loaded: Boolean(existing?.loaded),
        failureId: Number(requestError.failureId) || 0,
        incidentId: Number(requestError.incidentId) || 0,
      };
      resourceRequestsRef.current.set(resourceKey, failedRequest);
      setResourceStatus((status) => ({
        ...status,
        [resourceKey]: {
          loading: true,
          loaded: Boolean(existing?.loaded),
          error: messageText,
        },
      }));
      if (transient) {
        const recoveredGeneration = getConnectivityIncidentRecoveryGeneration(
          failedRequest.incidentId,
          failedRequest.failureId,
        );
        if (recoveredGeneration > failedRequest.lastRecoveryGeneration) {
          failedRequest.lastRecoveryGeneration = recoveredGeneration;
          queueMicrotask(() => {
            const current = resourceRequestsRef.current.get(resourceKey);
            if (
              mountedRef.current
              && currentIdentityRef.current === settingsIdentity
              && current?.requestToken === requestToken
            ) {
              void loadSettingsResource(resourceKey, {
                force: true,
                recoveryGeneration: recoveredGeneration,
              });
            }
          });
        }
      }
    } finally {
      const current = resourceRequestsRef.current.get(resourceKey);
      if (
        mountedRef.current
        && currentIdentityRef.current === settingsIdentity
        && current?.requestToken === requestToken
      ) {
        current.loading = false;
        current.controller = null;
        setResourceStatus((status) => ({
          ...status,
          [resourceKey]: {
            ...status[resourceKey],
            loading: false,
            loaded: Boolean(current.loaded || status[resourceKey]?.loaded),
          },
        }));
      }
    }
  }, [
    refreshHiddenLists,
    settingsIdentity,
    showDesktopControlCenter,
    user?.id,
    user?.role,
  ]);

  useEffect(() => {
    const resources = settingsResourcesForSection(
      activeSettingsSection,
      user?.role,
      desktopSettingsTab,
    );
    resources.forEach((resourceKey) => {
      void loadSettingsResource(resourceKey);
    });
  }, [activeSettingsSection, desktopSettingsTab, loadSettingsResource, user?.role]);

  useEffect(() => {
    const handleConnectivityRecovered = (event) => {
      const detail = event?.detail || {};
      const recoveredGeneration = Number(detail.generation) || 0;
      for (const [resourceKey, request] of resourceRequestsRef.current.entries()) {
        if (
          !request.transient
          || request.identity !== settingsIdentity
          || request.lastRecoveryGeneration >= recoveredGeneration
          || (request.incidentId && request.incidentId !== Number(detail.incidentId))
          || (request.failureId && request.failureId > Number(detail.recoveredThroughFailureId))
        ) {
          continue;
        }
        request.lastRecoveryGeneration = recoveredGeneration;
        void loadSettingsResource(resourceKey, {
          force: true,
          recoveryGeneration: recoveredGeneration,
        });
      }
      const pending = pendingHiddenReconciliationRef.current;
      if (
        pending
        && !pending.expired
        && Date.now() < pending.expiresAt
        && Number(pending.lastRecoveryGeneration || 0) < recoveredGeneration
        && (!pending.incidentId || pending.incidentId === Number(detail.incidentId))
        && (!pending.failureId || pending.failureId <= Number(detail.recoveredThroughFailureId))
      ) {
        pending.lastRecoveryGeneration = recoveredGeneration;
        void pendingReconcileHandlerRef.current?.(pending);
      }
    };
    window.addEventListener(CONNECTIVITY_RECOVERED_EVENT, handleConnectivityRecovered);
    return () => window.removeEventListener(CONNECTIVITY_RECOVERED_EVENT, handleConnectivityRecovered);
  }, [loadSettingsResource, settingsIdentity]);

  useEffect(() => {
    if (user?.role !== "admin") {
      setSharedMediaLibraryReference({
        configured_value: null,
        effective_value: "",
        default_value: "",
        configured_locations: [],
        effective_locations: [],
        category_summary: {},
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
    }
  }, [user?.role]);

  useLayoutEffect(() => {
    const pathState = classifyControlCenterPath(location.pathname);
    const resolution = pathState.area === "settings" && pathState.tab
      ? {
          section: desktopSettingsTabToLegacySection(pathState.tab, user?.role),
          shouldReplace: false,
          needsStorageMigration: false,
          storageMigrationTarget: null,
        }
      : resolveSettingsSection({ search: location.search, hash: location.hash });
    applySettingsSectionStorageMigration(resolution);
    writePersistedSettingsSection(resolution.section);
    if (activeSettingsSection !== resolution.section) {
      setActiveSettingsSection(resolution.section);
      setActiveSettingsButtonExpanded(true);
    }
  }, [
    activeSettingsSection,
    location,
    navigate,
    user?.role,
  ]);

  useLayoutEffect(() => {
    if (
      activeSettingsSection !== "libraries"
      || location.hash !== "#hidden-list"
      || (!resourceStatus.hidden.loaded && !resourceStatus.hidden.error)
    ) {
      return undefined;
    }
    const restoreKey = [
      settingsIdentity,
      location.key,
      location.pathname,
      location.search,
      location.hash,
    ].join("|");
    if (hiddenHashRestoreKeyRef.current === restoreKey) {
      return undefined;
    }

    let cancelled = false;
    let frameId = 0;
    let correctionCount = 0;
    const cancelRestore = () => {
      cancelled = true;
      hiddenHashRestoreKeyRef.current = restoreKey;
      if (frameId) {
        window.cancelAnimationFrame(frameId);
      }
    };
    const cancelEvents = ["wheel", "touchstart", "pointerdown", "keydown"];
    cancelEvents.forEach((eventName) => {
      window.addEventListener(eventName, cancelRestore, { capture: true, passive: true });
    });

    const correctTarget = () => {
      if (cancelled) {
        return;
      }
      const target = document.getElementById("hidden-list");
      if (!target) {
        return;
      }
      if (correctionCount === 0) {
        target.scrollIntoView?.({ block: "start" });
      } else {
        const top = target.getBoundingClientRect().top;
        const scrollMarginTop = Number.parseFloat(
          window.getComputedStyle(target).scrollMarginTop,
        ) || 0;
        const correction = top - scrollMarginTop;
        if (Math.abs(correction) > 8) {
          window.scrollBy?.({ top: correction, behavior: "auto" });
        }
      }
      correctionCount += 1;
      if (correctionCount < 2) {
        frameId = window.requestAnimationFrame(correctTarget);
      } else {
        hiddenHashRestoreKeyRef.current = restoreKey;
      }
    };
    frameId = window.requestAnimationFrame(correctTarget);

    return () => {
      if (frameId) {
        window.cancelAnimationFrame(frameId);
      }
      cancelEvents.forEach((eventName) => {
        window.removeEventListener(eventName, cancelRestore, { capture: true });
      });
    };
  }, [
    activeSettingsSection,
    location.hash,
    location.key,
    location.pathname,
    location.search,
    resourceStatus.hidden.error,
    resourceStatus.hidden.loaded,
    settingsIdentity,
  ]);

  useLayoutEffect(() => {
    const googleSetupReady = resourceStatus.googleSetup.loaded || resourceStatus.googleSetup.error;
    const cloudReady = resourceStatus.cloud.loaded || resourceStatus.cloud.error;
    if (
      activeSettingsSection !== "advanced"
      || user?.role !== "admin"
      || location.hash !== "#google-drive-oauth-setup"
      || !googleSetupReady
      || !cloudReady
    ) {
      return undefined;
    }
    const restoreKey = [
      settingsIdentity,
      location.key,
      location.pathname,
      location.search,
      location.hash,
    ].join("|");
    if (oauthHashRestoreKeyRef.current === restoreKey) {
      return undefined;
    }

    let cancelled = false;
    let frameId = 0;
    let correctionCount = 0;
    const cancelRestore = () => {
      cancelled = true;
      oauthHashRestoreKeyRef.current = restoreKey;
      if (frameId) {
        window.cancelAnimationFrame(frameId);
      }
    };
    const cancelEvents = ["wheel", "touchstart", "pointerdown", "keydown"];
    cancelEvents.forEach((eventName) => {
      window.addEventListener(eventName, cancelRestore, { capture: true, passive: true });
    });

    const correctTarget = () => {
      if (cancelled) {
        return;
      }
      const target = document.getElementById("google-drive-oauth-setup");
      if (!target) {
        return;
      }
      if (correctionCount === 0) {
        target.scrollIntoView?.({ block: "start" });
      } else {
        const top = target.getBoundingClientRect().top;
        const scrollMarginTop = Number.parseFloat(
          window.getComputedStyle(target).scrollMarginTop,
        ) || 0;
        const correction = top - scrollMarginTop;
        if (Math.abs(correction) > 8) {
          window.scrollBy?.({ top: correction, behavior: "auto" });
        }
      }
      correctionCount += 1;
      if (correctionCount < 2) {
        frameId = window.requestAnimationFrame(correctTarget);
      } else {
        oauthHashRestoreKeyRef.current = restoreKey;
      }
    };
    frameId = window.requestAnimationFrame(correctTarget);

    return () => {
      if (frameId) {
        window.cancelAnimationFrame(frameId);
      }
      cancelEvents.forEach((eventName) => {
        window.removeEventListener(eventName, cancelRestore, { capture: true });
      });
    };
  }, [
    activeSettingsSection,
    location.hash,
    location.key,
    location.pathname,
    location.search,
    resourceStatus.cloud.error,
    resourceStatus.cloud.loaded,
    resourceStatus.googleSetup.error,
    resourceStatus.googleSetup.loaded,
    settingsIdentity,
    user?.role,
  ]);

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
      void invalidateLibraryQueries();
      void loadSettingsResource("cloud", { force: true });
      if (user?.role === "admin") {
        void loadSettingsResource("googleSetup", { force: true });
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
    navigate(nextUrl, { replace: true, state: location.state });
  }, [
    loadSettingsResource,
    location.hash,
    location.pathname,
    location.search,
    location.state,
    navigate,
    user?.role,
  ]);

  function applyUserSettingsPayload(payload) {
    const resolvedSettings = resolveUserSettings(payload);
    setUserSettingsQueryData(user, resolvedSettings);
    setSettings(resolvedSettings);
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(USER_SETTINGS_CHANGED_EVENT, { detail: resolvedSettings }));
    }
    return resolvedSettings;
  }

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
      applyUserSettingsPayload(payload);
      void invalidateLibraryQueries();
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
      applyUserSettingsPayload(payload);
      void invalidateLibraryQueries();
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

  async function handleDesktopFloatingIslandPositionChange(nextValue) {
    const normalizedValue = nextValue === "bottom" ? "bottom" : "top";
    if (normalizedValue === settings.desktop_floating_island_position) {
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const payload = await apiRequest("/api/user-settings", {
        method: "PATCH",
        data: { desktop_floating_island_position: normalizedValue },
      });
      applyUserSettingsPayload(payload);
      setMessage(`Desktop Floating Island moved to the ${normalizedValue}.`);
    } catch (requestError) {
      setError(requestError.message || "Failed to update Desktop Floating Island position");
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
      applyUserSettingsPayload(payload);
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
      applyUserSettingsPayload(payload);
      setMessage("Poster display quality saved.");
    } catch (requestError) {
      setError(requestError.message || "Failed to update poster display quality");
    } finally {
      setSaving(false);
    }
  }

  function applyBackgroundPayload(payload, successMessage) {
    const normalizedBackground = normalizeUserBackgroundSettings(payload);
    applyUserSettingsPayload(payload);
    setBackgroundDraft(normalizedBackground);
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
          background_custom_model: "legacy_v1",
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
        background_custom_model: "legacy_v1",
        background_gradient_start: draft.background_gradient_start,
        background_gradient_end: gradientEnd,
        background_gradient_accent: draft.background_gradient_accent,
      },
      "Gradient background saved.",
    );
  }

  async function handleDesktopHueSave() {
    const draft = normalizeUserBackgroundSettings(backgroundDraft);
    if (draft.background_mode === "solid") {
      await patchBackgroundSettings(
        {
          background_mode: "solid",
          background_custom_model: "hue_v2",
          background_solid_hue: draft.background_solid_hue,
          background_solid_color: hueToHex(draft.background_solid_hue, 45, 30),
        },
        "Solid background saved.",
      );
      return;
    }
    await patchBackgroundSettings(
      {
        background_mode: "gradient",
        background_custom_model: "hue_v2",
        background_gradient_start_hue: draft.background_gradient_start_hue,
        background_gradient_end_hue: draft.background_gradient_end_hue,
        background_gradient_start: hueToHex(draft.background_gradient_start_hue, 62, 42),
        background_gradient_end: hueToHex(draft.background_gradient_end_hue, 60, 22),
        background_gradient_accent: hueToHex(
          Math.round((draft.background_gradient_start_hue + draft.background_gradient_end_hue) / 2),
          61,
          32,
        ),
      },
      "Gradient background saved.",
    );
  }

  function resetDesktopHueDraft() {
    const savedBackground = normalizeUserBackgroundSettings(settings);
    setBackgroundDraft((current) => ({
      ...current,
      background_custom_model: "hue_v2",
      background_gradient_start_hue: savedBackground.background_gradient_start_hue,
      background_gradient_end_hue: savedBackground.background_gradient_end_hue,
      background_solid_hue: savedBackground.background_solid_hue,
    }));
    setBackgroundError("");
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
      void invalidateLibraryQueries();
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
      void invalidateLibraryQueries();
    } catch (requestError) {
      setError(requestError.message || "Failed to restore globally hidden movie");
    } finally {
      setRestoringGlobalItemId(null);
    }
  }

  async function handleHideUniversally(hiddenItem) {
    if (pendingHiddenReconciliationRef.current?.itemId === hiddenItem.id) {
      return;
    }
    setMovingToGlobalItemId(hiddenItem.id);
    setError("");
    setMessage("");
    const successMessage = "This movie is hidden for everyone.";
    try {
      const payload = await apiRequest(`/api/admin/hidden-items/${hiddenItem.id}/scope`, {
        method: "PUT",
        data: { target_scope: "global" },
      });
      setHiddenItems((current) => current.filter((item) => item.id !== hiddenItem.id));
      setGlobalHiddenItems((current) => {
        const existing = current.find((item) => hiddenItemsShareIdentity(item, hiddenItem));
        if (existing) {
          return current.map((item) => (
            hiddenItemsShareIdentity(item, hiddenItem)
              ? { ...item, hidden_at: payload.hidden_at }
              : item
          ));
        }
        return [
          {
            ...hiddenItem,
            hidden_at: payload.hidden_at,
          },
          ...current,
        ];
      });
      setMessage(payload.message || successMessage);
      void invalidateLibraryQueries();
      void refreshHiddenLists({ ownerKind: "mutation_refresh" });
    } catch (requestError) {
      if (isUncertainHiddenScopeError(requestError)) {
        const startedAt = Date.now();
        const pending = {
          token: pendingHiddenReconciliationTokenRef.current + 1,
          itemId: hiddenItem.id,
          identity: settingsIdentity,
          requestedScope: "global",
          sourceScope: "personal",
          incidentId: Number(requestError.incidentId) || 0,
          failureId: Number(requestError.failureId) || 0,
          startedAt,
          expiresAt: startedAt + HIDDEN_RECONCILIATION_MAX_AGE_MS,
          lastRecoveryGeneration: 0,
          reconciling: false,
          expired: false,
          errorMessage: requestError.message || "Failed to hide this movie for everyone",
        };
        pendingHiddenReconciliationTokenRef.current = pending.token;
        clearPendingHiddenExpiryTimer();
        pendingHiddenReconciliationRef.current = pending;
        setPendingHiddenReconciliation(pending);
        schedulePendingHiddenExpiry(pending);
        if (await reconcilePendingHiddenScope(pending)) {
          return;
        }
        setError("");
        return;
      }
      setError(requestError.message || "Failed to hide this movie for everyone");
    } finally {
      setMovingToGlobalItemId(null);
    }
  }

  async function handleHideForMe(hiddenItem) {
    if (pendingHiddenReconciliationRef.current?.itemId === hiddenItem.id) {
      return;
    }
    setMovingToPersonalItemId(hiddenItem.id);
    setError("");
    setMessage("");
    const successMessage = "This movie is now hidden only for your account.";
    try {
      const payload = await apiRequest(`/api/admin/hidden-items/${hiddenItem.id}/scope`, {
        method: "PUT",
        data: { target_scope: "personal" },
      });
      setGlobalHiddenItems((current) => current.filter((item) => item.id !== hiddenItem.id));
      setHiddenItems((current) => {
        const existing = current.find((item) => hiddenItemsShareIdentity(item, hiddenItem));
        if (existing) {
          return current.map((item) => (
            hiddenItemsShareIdentity(item, hiddenItem)
              ? { ...item, hidden_at: payload.hidden_at }
              : item
          ));
        }
        return [
          {
            ...hiddenItem,
            hidden_at: payload.hidden_at,
          },
          ...current,
        ];
      });
      setMessage(payload.message || successMessage);
      void invalidateLibraryQueries();
      void refreshHiddenLists({ ownerKind: "mutation_refresh" });
    } catch (requestError) {
      if (isUncertainHiddenScopeError(requestError)) {
        const startedAt = Date.now();
        const pending = {
          token: pendingHiddenReconciliationTokenRef.current + 1,
          itemId: hiddenItem.id,
          identity: settingsIdentity,
          requestedScope: "personal",
          sourceScope: "global",
          incidentId: Number(requestError.incidentId) || 0,
          failureId: Number(requestError.failureId) || 0,
          startedAt,
          expiresAt: startedAt + HIDDEN_RECONCILIATION_MAX_AGE_MS,
          lastRecoveryGeneration: 0,
          reconciling: false,
          expired: false,
          errorMessage: requestError.message || "Failed to hide this movie only for your account",
        };
        pendingHiddenReconciliationTokenRef.current = pending.token;
        clearPendingHiddenExpiryTimer();
        pendingHiddenReconciliationRef.current = pending;
        setPendingHiddenReconciliation(pending);
        schedulePendingHiddenExpiry(pending);
        if (await reconcilePendingHiddenScope(pending)) {
          return;
        }
        setError("");
        return;
      }
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
      void invalidateLibraryQueries();
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
      setMessage("Library reference locations saved.");
      void invalidateLibraryQueries();
    } catch (requestError) {
      setError(requestError.message || "Failed to save library reference locations");
    } finally {
      setSharedMediaLibraryReferenceSaving(false);
    }
  }

  async function loadDirectoryPicker(target, path) {
    setDirectoryPicker((current) => ({
      ...current,
      open: true,
      target,
      title: target === "poster-reference" ? "Browse poster directories" : "Browse library reference directories",
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
        title: target === "poster-reference" ? "Browse poster directories" : "Browse library reference directories",
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
        title: target === "poster-reference" ? "Browse poster directories" : "Browse library reference directories",
        loading: false,
        error: requestError.message || "Failed to browse server directories",
      }));
    }
  }

  async function handleOpenDirectoryPicker(target) {
    const platform = detectClientPlatform();
    const sameHostHint = isSettingsLocalDevelopmentLoopback(platform);
    const initialPath = target === "poster-reference"
      ? posterReferenceInput || posterReference.effective_value || posterReference.default_value || ""
      : firstNonEmptyLine(sharedMediaLibraryReferenceInput)
        || firstNonEmptyLine(sharedMediaLibraryReference.effective_value)
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
          purpose: directoryPickerPurposeForTarget(target),
          platform,
          same_host_hint: sameHostHint,
        },
      });
      if (payload?.status === "selected" && payload?.selected_path) {
        if (target === "poster-reference") {
          setPosterReferenceInput(payload.selected_path);
        } else {
          setSharedMediaLibraryReferenceInput((current) =>
            appendUniqueReferenceLocation(current, payload.selected_path));
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
      setSharedMediaLibraryReferenceInput((current) =>
        appendUniqueReferenceLocation(current, directoryPicker.current_path));
    }
    handleCloseDirectoryPicker();
  }

  async function refreshCloudLibraries() {
    const payload = showDesktopControlCenter && user?.role === "admin"
      ? await fetchControlCenterResource({
        userId: user?.id,
        role: user?.role,
        resource: "cloudLibraries",
        force: true,
      })
      : await apiRequest("/api/cloud-libraries");
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

  function handleOpenAgeBucket(bucket) {
    setAgeBucketManager({
      open: true,
      age: bucket.age,
      savingKey: "",
      error: "",
    });
  }

  async function handleOpenAgeGroupFromBucket(group) {
    setAgeBucketManager((current) => ({ ...current, open: false, error: "", savingKey: "" }));
    await handleOpenAgeGroupManager(group);
  }

  async function handleRemoveAgeRequirementFromBucket(group) {
    if (!group?.primary_media_item_id || ageBucketManager.savingKey) {
      return;
    }
    setAgeBucketManager((current) => ({
      ...current,
      savingKey: group.age_group_key,
      error: "",
    }));
    try {
      await apiRequest(`/api/library/item/${group.primary_media_item_id}/age-requirement`, {
        method: "PATCH",
        data: { age_requirement: null },
      });
      await refreshAgeGroups();
      setMessage("Age requirement removed.");
      setError("");
      setAgeBucketManager((current) => ({ ...current, savingKey: "", error: "" }));
      void invalidateLibraryQueries();
    } catch (requestError) {
      setAgeBucketManager((current) => ({
        ...current,
        savingKey: "",
        error: requestError.message || "Failed to remove age requirement",
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
      void invalidateLibraryQueries();
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
      void invalidateLibraryQueries();
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
      void invalidateLibraryQueries();
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
    const responsePayload = showDesktopControlCenter
      ? await fetchControlCenterResource({
        userId: user?.id,
        role: user?.role,
        resource: "googleDriveSetup",
        force: true,
      })
      : await apiRequest("/api/admin/google-drive-setup");
    const payload = sanitizeGoogleDriveSetupPayload(responsePayload);
    setGoogleDriveSetup(payload);
    setGoogleDriveSetupDraft({
      https_origin: payload.https_origin || "",
      client_id: payload.client_id || "",
      client_secret: "",
    });
    return payload;
  }

  async function handleGoogleDriveSetupSave(event) {
    event.preventDefault();
    setGoogleDriveSetupSaving(true);
    setError("");
    setMessage("");
    try {
      const responsePayload = await apiRequest("/api/admin/google-drive-setup", {
        method: "PUT",
        data: {
          https_origin: googleDriveSetupDraft.https_origin,
          client_id: googleDriveSetupDraft.client_id,
          client_secret: googleDriveSetupDraft.client_secret,
        },
      });
      const payload = sanitizeGoogleDriveSetupPayload(responsePayload);
      setGoogleDriveSetup(payload);
      if (showDesktopControlCenter) {
        setControlCenterResourceData({
          userId: user?.id,
          role: user?.role,
          resource: "googleDriveSetup",
        }, payload);
      }
      setGoogleDriveSetupDraft({
        https_origin: payload.https_origin || "",
        client_id: payload.client_id || "",
        client_secret: "",
      });
      const successMessage = payload.configuration_state === "ready"
        ? "Google Drive setup saved. You can connect Google Drive below."
        : "Google Drive setup saved.";
      try {
        await refreshCloudLibraries();
        void invalidateLibraryQueries();
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

  async function handleGoogleDestructiveAction() {
    const action = googleDestructiveAction;
    if (!action || googleDriveSetupSaving) {
      return;
    }
    setGoogleDriveSetupSaving(true);
    setError("");
    setMessage("");
    try {
      const endpoint = action === "clear-setup"
        ? "/api/admin/google-drive-setup"
        : "/api/admin/google-drive-account";
      const responsePayload = await apiRequest(endpoint, { method: "DELETE" });
      const payload = sanitizeGoogleDriveSetupPayload(responsePayload);
      setGoogleDriveSetup(payload);
      if (showDesktopControlCenter) {
        setControlCenterResourceData({
          userId: user?.id,
          role: user?.role,
          resource: "googleDriveSetup",
        }, payload);
      }
      setGoogleDriveSetupDraft({
        https_origin: payload.https_origin || "",
        client_id: payload.client_id || "",
        client_secret: "",
      });
      await refreshCloudLibraries();
      void invalidateLibraryQueries();
      setMessage(action === "clear-setup"
        ? "Saved OAuth overrides cleared."
        : "Google account disconnected. Cloud sources were kept.");
      setGoogleDestructiveAction("");
    } catch (requestError) {
      setError(requestError.message || "Failed to update Google Drive configuration");
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
        void invalidateLibraryQueries();
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
      setCloudLibraries((current) => {
        const next = {
          ...current,
          shared_libraries: current.shared_libraries.map((entry) =>
            entry.id === source.id ? { ...entry, hidden_for_user: nextHidden } : entry,
          ),
        };
        if (showDesktopControlCenter && user?.role === "admin") {
          setControlCenterResourceData({
            userId: user?.id,
            role: user?.role,
            resource: "cloudLibraries",
          }, next);
        }
        return next;
      });
      setMessage(
        payload.message
          || (nextHidden ? "This shared library is hidden for your account." : "This shared library is visible again."),
      );
      void invalidateLibraryQueries();
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
          const next = {
            ...current,
            my_libraries: nextMyLibraries,
            shared_libraries: sortCloudSources([updated, ...nextSharedLibraries]),
          };
          if (showDesktopControlCenter && user?.role === "admin") {
            setControlCenterResourceData({
              userId: user?.id,
              role: user?.role,
              resource: "cloudLibraries",
            }, next);
          }
          return next;
        }
        const next = {
          ...current,
          my_libraries: sortCloudSources([updated, ...nextMyLibraries]),
          shared_libraries: nextSharedLibraries,
        };
        if (showDesktopControlCenter && user?.role === "admin") {
          setControlCenterResourceData({
            userId: user?.id,
            role: user?.role,
            resource: "cloudLibraries",
          }, next);
        }
        return next;
      });
      setMessage(nextShared ? "Library shared globally." : "Library moved back to My Libraries.");
      await refreshCloudLibraries();
      void invalidateLibraryQueries();
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
    if (activeSettingsSection === sectionKey) {
      setActiveSettingsButtonExpanded((currentExpanded) => !currentExpanded);
      return;
    }
    writePersistedSettingsSection(sectionKey);
    setActiveSettingsSection(sectionKey);
    setActiveSettingsButtonExpanded(true);
    navigate(buildSettingsSectionLocation(location, sectionKey), {
      replace: true,
      state: location.state,
    });
  }

  function activateSettingsPanel(sectionKey) {
    if (activeSettingsSection === sectionKey) {
      return;
    }
    writePersistedSettingsSection(sectionKey);
    setActiveSettingsSection(sectionKey);
    setActiveSettingsButtonExpanded(true);
    navigate(buildSettingsSectionLocation(location, sectionKey), {
      replace: true,
      state: location.state,
    });
  }

  function handleSettingsTabKeyDown(event, sectionIndex) {
    const lastIndex = SETTINGS_SECTIONS.length - 1;
    let nextIndex = null;
    if (event.key === "ArrowRight") {
      nextIndex = sectionIndex === lastIndex ? 0 : sectionIndex + 1;
    } else if (event.key === "ArrowLeft") {
      nextIndex = sectionIndex === 0 ? lastIndex : sectionIndex - 1;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = lastIndex;
    }
    if (nextIndex === null) {
      return;
    }
    event.preventDefault();
    const nextSection = SETTINGS_SECTIONS[nextIndex];
    const nextTab = event.currentTarget
      .closest('[role="tablist"]')
      ?.querySelector(`#settings-tab-${nextSection.key}`);
    nextTab?.focus();
    activateSettingsPanel(nextSection.key);
  }

  if (showDesktopControlCenter) {
    const meridianModel = {
      error: userSettingsLoadError || error,
      message,
      resourceErrors: activeResourceErrors,
      appearance: {
        backgroundDraft,
        backgroundError,
        backgroundSaving,
        loading,
        saving,
        settings: {
          ...settings,
          poster_card_appearance: normalizePosterCardAppearance(settings.poster_card_appearance),
          poster_card_display_max_width: normalizePosterDisplayWidth(settings.poster_card_display_max_width),
        },
        posterAppearanceOptions: POSTER_CARD_APPEARANCE_OPTIONS,
        posterWidthOptions: POSTER_DISPLAY_WIDTH_OPTIONS,
        onPosterAppearanceChange: handlePosterCardAppearanceChange,
        onIslandPositionChange: handleDesktopFloatingIslandPositionChange,
        onPosterWidthChange: handlePosterDisplayWidthChange,
        onBackgroundModeChange: handleBackgroundModeChange,
        onBackgroundPresetSelect: handleBackgroundPresetSelect,
        onBackgroundDraftChange(key, value) {
          setBackgroundDraft((current) => ({
            ...current,
            background_custom_model: "hue_v2",
            [key]: value,
          }));
          setBackgroundError("");
        },
        onHueSave: handleDesktopHueSave,
        onHueReset: resetDesktopHueDraft,
        onPhotoUpload: handleBackgroundPhotoUpload,
        onBackgroundResetRequest: requestBackgroundReset,
      },
      library: {
        settings,
        saving,
        isAdmin: user?.role === "admin",
        ageGroupsLoading,
        ageBuckets: restrictedAgeBuckets,
        onDuplicateToggle: handleDuplicateToggle,
        onRecentlyAddedToggle: handleRecentlyAddedToggle,
        onRefreshAgeGroups: refreshAgeGroups,
        onOpenAgeBucket: handleOpenAgeBucket,
      },
      cloud: {
        isAdmin: user?.role === "admin",
        username: user?.username,
        cloudLibraries,
        cloudBusyKey,
        myLibraryDraft,
        sharedLibraryDraft,
        setMyLibraryDraft,
        setSharedLibraryDraft,
        onGoogleConnect: handleGoogleDriveConnect,
        onAddCloudSource: handleAddCloudSource,
        onMoveCloudSource: handleMoveCloudSource,
        onSharedVisibilityToggle: handleSharedLibraryVisibilityToggle,
        formatCloudTimestamp,
      },
      hidden: {
        isAdmin: user?.role === "admin",
        hiddenItems,
        globalHiddenItems,
        hiddenLoading,
        hiddenExpanded: hiddenListsExpanded,
        onHiddenExpandedChange(scope, expanded) {
          setHiddenListsExpanded((current) => ({
            ...current,
            [scope === "personal" ? "personal" : "global"]: expanded,
          }));
        },
        onShowAgain: handleShowAgain,
        onShowForEveryone: handleShowForEveryone,
        onHideForEveryone: handleHideUniversally,
        onHideForMe: handleHideForMe,
      },
      playbackPanel: <InstallSettingsPanel presentation="meridian" />,
      server: {
        googleSetup: googleDriveSetup,
        googleSetupDraft: googleDriveSetupDraft,
        setGoogleSetupDraft: setGoogleDriveSetupDraft,
        googleSetupSaving: googleDriveSetupSaving,
        googleSetupBadgeLabel,
        googleConnectionHealth: formatGoogleConnectionHealthLabel(cloudLibraries.google),
        sourceHealth: visibleCloudSourceHealthLabel,
        onGoogleSetupSave: handleGoogleDriveSetupSave,
        onCopyGoogleCallback: handleCopyGoogleDriveCallback,
        secretInput: (
          <NonLoginSecretInput
            disabled={googleDriveSetupSaving}
            onChange={(event) => setGoogleDriveSetupDraft((current) => ({ ...current, client_secret: event.target.value }))}
            placeholder={googleDriveSetup.client_secret_configured ? "Leave blank to keep the saved secret" : "Enter the Google OAuth client secret"}
            purpose="google-oauth-client-secret"
            value={googleDriveSetupDraft.client_secret}
          />
        ),
        sharedReference: sharedMediaLibraryReference,
        sharedReferenceInput: sharedMediaLibraryReferenceInput,
        sharedReferenceSaving: sharedMediaLibraryReferenceSaving,
        setSharedReferenceInput: setSharedMediaLibraryReferenceInput,
        onSharedReferenceSave: handleSharedMediaLibraryReferenceSave,
        posterReference,
        posterReferenceInput,
        posterReferenceSaving,
        setPosterReferenceInput,
        onPosterReferenceSave: handlePosterReferenceSave,
        onOpenDirectoryPicker: handleOpenDirectoryPicker,
      },
    };
    return (
      <section className="meridian-page meridian-settings-page">
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
          onAgeRequirementChange={(value) => setAgeGroupManager((current) => ({ ...current, ageRequirementValue: value }))}
          onClose={() => setAgeGroupManager((current) => ({ ...current, open: false }))}
          onLinkItem={handleLinkAgeGroupItem}
          onSaveAgeRequirement={handleSaveAgeGroupRequirement}
          onSearch={handleSearchAgeGroupCandidates}
          onSearchQueryChange={(value) => setAgeGroupManager((current) => ({ ...current, searchQuery: value }))}
          onUnlinkItem={handleUnlinkAgeGroupItem}
          open={ageGroupManager.open}
          saving={ageGroupManager.saving}
          searchQuery={ageGroupManager.searchQuery}
          searchResults={ageGroupManager.searchResults}
          searching={ageGroupManager.searching}
        />
        <AgeBucketManagerModal
          bucket={activeAgeBucket}
          error={ageBucketManager.error}
          onClose={() => setAgeBucketManager((current) => ({ ...current, open: false, savingKey: "", error: "" }))}
          onManageGroup={handleOpenAgeGroupFromBucket}
          onRemoveRequirement={handleRemoveAgeRequirementFromBucket}
          open={ageBucketManager.open}
          savingKey={ageBucketManager.savingKey}
        />
        {totpStatus?.setup_available && !totpStatus?.enabled ? (
          <div className="meridian-notice" role="status"><span>Two-factor setup is still required for this account.</span><button onClick={() => navigate("/setup/totp")} type="button">Set up 2FA</button></div>
        ) : null}
        <MeridianSettingsView model={meridianModel} tab={desktopSettingsTab} />
      </section>
    );
  }

  if (settingsSectionResolution.shouldReplace) {
    return (
      <Navigate
        replace
        state={location.state}
        to={buildSettingsSectionLocation(location, settingsSectionResolution.section)}
      />
    );
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

      <AgeBucketManagerModal
        bucket={activeAgeBucket}
        error={ageBucketManager.error}
        onClose={() => setAgeBucketManager((current) => ({ ...current, open: false, savingKey: "", error: "" }))}
        onManageGroup={handleOpenAgeGroupFromBucket}
        onRemoveRequirement={handleRemoveAgeRequirementFromBucket}
        open={ageBucketManager.open}
        savingKey={ageBucketManager.savingKey}
      />

      {!showDesktopControlCenter ? (
      <div className="admin-nav-card settings-section-nav-card" aria-label="Settings sections">
        <div
          aria-orientation="horizontal"
          className="admin-nav-card__actions settings-section-nav-card__actions"
          role="tablist"
        >
          {SETTINGS_SECTIONS.map((section, sectionIndex) => {
            const isActive = activeSettingsSection === section.key;
            return (
              <button
                aria-label={section.label}
                aria-controls={`settings-panel-${section.key}`}
                aria-selected={isActive}
                className={[
                  "admin-nav-card__button",
                  isActive ? "admin-nav-card__button--active" : "",
                  isActive && activeSettingsButtonExpanded ? "admin-nav-card__button--expanded" : "",
                ].filter(Boolean).join(" ")}
                id={`settings-tab-${section.key}`}
                key={section.key}
                onClick={() => handleSettingsPanelToggle(section.key)}
                onKeyDown={(event) => handleSettingsTabKeyDown(event, sectionIndex)}
                role="tab"
                tabIndex={isActive ? 0 : -1}
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
        <DesktopBackToLibraryButton className="admin-nav-card__back" />
      </div>
      ) : null}

      {activeSettingsSection !== "install" && userSettingsLoadError ? (
        <p className="form-error settings-user-settings-load-error">
          {userSettingsLoadError}
        </p>
      ) : null}
      {activeSettingsSection !== "install" && error ? <p className="form-error">{error}</p> : null}
      {activeSettingsSection !== "install" && message ? <p className="page-note">{message}</p> : null}
      {activeSettingsSection !== "install" && activeResourceErrors.length > 0 ? (
        <div className="settings-resource-errors" role="status" aria-live="polite">
          {activeResourceErrors.map((resourceError) => (
            <div className="settings-resource-error" key={resourceError.key}>
              <span>{resourceError.message}</span>
              <button
                className="ghost-button ghost-button--inline"
                onClick={() => loadSettingsResource(resourceError.key, { force: true })}
                type="button"
              >
                Retry
              </button>
            </div>
          ))}
        </div>
      ) : null}
      {pendingHiddenReconciliation ? (
        <div className="settings-resource-error" role="status" aria-live="polite">
          <span>
            {pendingHiddenReconciliation.expired
              ? "Could not confirm the change. Refresh or retry confirmation."
              : "Waiting to confirm the hidden scope change."}
          </span>
          <button
            className="ghost-button ghost-button--inline"
            onClick={() => reconcilePendingHiddenScope(undefined, { manual: true })}
            type="button"
          >
            Retry confirmation
          </button>
        </div>
      ) : null}

      {showDesktopControlCenter && totpStatus?.setup_available && !totpStatus?.enabled ? (
        <div className="control-center-notice" role="status">
          <span>Two-factor setup is still required for this account.</span>
          <button onClick={() => navigate("/setup/totp")} type="button">Set up 2FA</button>
        </div>
      ) : null}

      {activeSettingsSection === "preferences"
        || (showDesktopControlCenter && desktopSettingsTab === "library") ? (
      <div
        aria-labelledby="settings-tab-preferences"
        className="settings-grid settings-grid--preferences settings-grid--compact-columns"
        id="settings-panel-preferences"
        role="tabpanel"
      >
        <div className="settings-grid__column">
          <section className="settings-card settings-display-card settings-preferences-poster-card">
            <div className="settings-inline-header">
              <div>
                <h2>Poster appearance</h2>
                <p className="page-subnote">Choose how movie cards appear in your library.</p>
              </div>
            </div>
            {loading ? (
              <p className="page-subnote">Loading display preferences...</p>
            ) : (
              <SettingsSegmentedControl
                ariaLabel="Poster appearance"
                disabled={saving}
                onChange={handlePosterCardAppearanceChange}
                options={POSTER_CARD_APPEARANCE_OPTIONS}
                value={normalizePosterCardAppearance(settings.poster_card_appearance)}
              />
            )}
          </section>

          {showDesktopFloatingIslandSettings ? (
          <section className="settings-card settings-display-interface-card settings-preferences-floating-card">
            <h2>Floating Island Position</h2>
            {loading ? (
              <p className="page-subnote">Loading interface preferences...</p>
            ) : (
              <div className="settings-card-stack">
                <SettingsSegmentedControl
                  ariaLabel="Desktop Floating Island position"
                  disabled={saving}
                  onChange={handleDesktopFloatingIslandPositionChange}
                  options={[
                    { value: "top", label: "Top" },
                    { value: "bottom", label: "Bottom" },
                  ]}
                  value={settings.desktop_floating_island_position === "bottom" ? "bottom" : "top"}
                />
              </div>
            )}
          </section>
          ) : null}

          {showDesktopControlCenter ? (
            <section className="settings-card control-center-settings-poster-width">
              <h2>Maximum poster width</h2>
              {loading ? (
                <p className="page-subnote">Loading poster display quality...</p>
              ) : (
                <label className="settings-field">
                  <span>
                    <strong>Library card images</strong>
                    <small>Choose the upper width used for poster card requests.</small>
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
              )}
            </section>
          ) : null}

          <section className="settings-card settings-display-library-card settings-preferences-library-card">
            <h2>Library</h2>
            {loading ? (
              <p className="page-subnote">Loading your library preferences...</p>
            ) : (
              <div className="settings-card-stack">
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
                <div className="settings-preferences-library-divider" />
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
              </div>
            )}
          </section>
        </div>

        <div className="settings-grid__column">
          <section className="settings-card settings-background-card settings-preferences-background-card">
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
                    {showDesktopControlCenter ? (
                      <div className="control-center-hue-editor">
                        <label>
                          <span>Start hue <strong>{backgroundDraft.background_gradient_start_hue}°</strong></span>
                          <input
                            disabled={backgroundSaving}
                            max="359"
                            min="0"
                            onChange={(event) => setBackgroundDraft((current) => ({
                              ...current,
                              background_custom_model: "hue_v2",
                              background_gradient_start_hue: Number(event.target.value),
                            }))}
                            type="range"
                            value={backgroundDraft.background_gradient_start_hue}
                          />
                        </label>
                        <label>
                          <span>End hue <strong>{backgroundDraft.background_gradient_end_hue}°</strong></span>
                          <input
                            disabled={backgroundSaving}
                            max="359"
                            min="0"
                            onChange={(event) => setBackgroundDraft((current) => ({
                              ...current,
                              background_custom_model: "hue_v2",
                              background_gradient_end_hue: Number(event.target.value),
                            }))}
                            type="range"
                            value={backgroundDraft.background_gradient_end_hue}
                          />
                        </label>
                        <div
                          aria-label="Custom gradient preview"
                          className="control-center-hue-editor__preview"
                          style={buildBackgroundPreviewStyle({ ...backgroundDraft, background_custom_model: "hue_v2" })}
                        />
                      </div>
                    ) : (
                      <BackgroundColorPicker
                        color={getBackgroundColorPickerValue(backgroundDraft)}
                        disabled={backgroundSaving}
                        mode="gradient"
                        onPick={handleBackgroundPalettePick}
                      />
                    )}
                    <div className="settings-background-actions">
                      <button
                        className="ghost-button ghost-button--inline"
                        disabled={backgroundSaving}
                        onClick={showDesktopControlCenter ? handleDesktopHueSave : handleBackgroundCustomSave}
                        type="button"
                      >
                        Save gradient
                      </button>
                      <button
                        className="ghost-button ghost-button--inline"
                        disabled={backgroundSaving}
                        onClick={showDesktopControlCenter ? resetDesktopHueDraft : requestBackgroundReset}
                        type="button"
                      >
                        Reset
                      </button>
                    </div>
                  </div>
                ) : null}

                {backgroundDraft.background_mode === "solid" ? (
                  <div className="settings-background-picker-panel">
                    {showDesktopControlCenter ? (
                      <div className="control-center-hue-editor">
                        <label>
                          <span>Hue <strong>{backgroundDraft.background_solid_hue}°</strong></span>
                          <input
                            disabled={backgroundSaving}
                            max="359"
                            min="0"
                            onChange={(event) => setBackgroundDraft((current) => ({
                              ...current,
                              background_custom_model: "hue_v2",
                              background_solid_hue: Number(event.target.value),
                            }))}
                            type="range"
                            value={backgroundDraft.background_solid_hue}
                          />
                        </label>
                        <div
                          aria-label="Custom solid background preview"
                          className="control-center-hue-editor__preview"
                          style={buildBackgroundPreviewStyle({ ...backgroundDraft, background_custom_model: "hue_v2" })}
                        />
                      </div>
                    ) : (
                      <BackgroundColorPicker
                        color={getBackgroundColorPickerValue(backgroundDraft)}
                        disabled={backgroundSaving}
                        mode="solid"
                        onPick={handleBackgroundPalettePick}
                      />
                    )}
                    <div className="settings-background-actions">
                      <button
                        className="ghost-button ghost-button--inline"
                        disabled={backgroundSaving}
                        onClick={showDesktopControlCenter ? handleDesktopHueSave : handleBackgroundCustomSave}
                        type="button"
                      >
                        Save solid
                      </button>
                      <button
                        className="ghost-button ghost-button--inline"
                        disabled={backgroundSaving}
                        onClick={showDesktopControlCenter ? resetDesktopHueDraft : requestBackgroundReset}
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
                    {settings.background_photo_url ? (
                      <p className="page-subnote">
                        {settings.background_photo_original_filename || "Background photo"}
                      </p>
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

        </div>
      </div>
      ) : null}

      {activeSettingsSection === "install" ? (
        <div
          aria-labelledby="settings-tab-install"
          id="settings-panel-install"
          role="tabpanel"
        >
          <InstallSettingsPanel />
        </div>
      ) : null}

      {activeSettingsSection === "libraries" ? (
      <div
        aria-labelledby="settings-tab-libraries"
        className="settings-grid"
        id="settings-panel-libraries"
        role="tabpanel"
      >
        {user?.role === "admin" ? (
          <section className="settings-card settings-card--wide settings-age-groups-card">
            <div
              aria-expanded={ageGroupsPanelOpen}
              className="settings-inline-header settings-age-groups-card__header"
              onClick={() => setAgeGroupsPanelOpen((current) => !current)}
            >
              <div>
                <h2>Age Groups</h2>
                <p className="page-subnote">Review automatic movie age groups and explicit manual links.</p>
              </div>
              <RefreshSweepButton
                className="ghost-button ghost-button--inline"
                onClick={(event) => {
                  event.stopPropagation();
                  refreshAgeGroups();
                }}
                type="button"
              >
                Refresh
              </RefreshSweepButton>
            </div>
            {ageGroupsPanelOpen && ageGroupsLoading ? (
              <p className="page-subnote">Loading age groups...</p>
            ) : ageGroupsPanelOpen && restrictedAgeBuckets.length > 0 ? (
              <div className="settings-age-bucket-list">
                {restrictedAgeBuckets.map((bucket) => (
                  <article className="settings-age-bucket-card" key={`age-bucket-${bucket.age}`}>
                    <span className="status-pill settings-age-bucket-card__age">{bucket.ageLabel}</span>
                    <div className="settings-age-group-row__copy">
                      <strong>{formatCountLabel(bucket.groupCount, "movie group")}</strong>
                      <small>
                        {formatCountLabel(bucket.copiesCount, "copy", "copies")}
                        {bucket.manualLinksCount ? ` · ${formatCountLabel(bucket.manualLinksCount, "manual link")}` : ""}
                      </small>
                    </div>
                    <button
                      className="ghost-button ghost-button--inline"
                      onClick={() => handleOpenAgeBucket(bucket)}
                      type="button"
                    >
                      Manage
                    </button>
                  </article>
                ))}
              </div>
            ) : ageGroupsPanelOpen ? (
              <div className="settings-age-group-empty">
                <strong>No age-restricted movies yet.</strong>
                <small>Set an age requirement from a movie's Info panel.</small>
              </div>
            ) : null}
          </section>
        ) : null}

        <SettingsAccordionSection
          className="control-center-settings-cloud"
          badge={cloudLibraries.my_libraries.length}
          description="Add your own Google Drive movie folders here. Personal cloud sources appear in your Library alongside DGX titles."
          isOpen={openSections.myLibraries}
          onToggle={() => toggleSection("myLibraries")}
          title="My Libraries"
        >
          {!cloudLibraries.google.enabled ? (
            <p className="page-subnote">
              Google Drive OAuth must be configured in Advanced before cloud libraries can connect.
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
          className="control-center-settings-cloud"
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

        <div className="settings-hidden-list-target" id="hidden-list">
        <section className="settings-card settings-card--wide control-center-settings-legacy-poster-width">
          <details className="settings-disclosure" open={showDesktopControlCenter || undefined}>
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
              {hiddenLoading ? (
                <p className="page-subnote">Loading hidden movies...</p>
              ) : hiddenItems.length > 0 ? (
                <div className="hidden-movie-list">
                  {visiblePersonalHiddenItems.map((hiddenItem) => (
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
                            disabled={
                              restoringItemId === hiddenItem.id
                              || movingToGlobalItemId === hiddenItem.id
                              || pendingHiddenReconciliation?.itemId === hiddenItem.id
                            }
                          onClick={() => handleShowAgain(hiddenItem.id)}
                          type="button"
                        >
                          {restoringItemId === hiddenItem.id ? "Restoring..." : "Show again"}
                        </button>
                        {user?.role === "admin" ? (
                          <button
                            className="ghost-button ghost-button--inline ghost-button--danger"
                            disabled={
                              movingToGlobalItemId === hiddenItem.id
                              || restoringItemId === hiddenItem.id
                              || pendingHiddenReconciliation?.itemId === hiddenItem.id
                            }
                            onClick={() => handleHideUniversally(hiddenItem)}
                            type="button"
                          >
                            {movingToGlobalItemId === hiddenItem.id ? "Hiding globally..." : "Hide universally"}
                          </button>
                        ) : null}
                      </div>
                    </article>
                  ))}
                  {showDesktopControlCenter && hiddenItems.length > 4 ? (
                    <button
                      className="control-center-list-expander"
                      onClick={() => setHiddenListsExpanded((current) => ({
                        ...current,
                        personal: !current.personal,
                      }))}
                      type="button"
                    >
                      {hiddenListsExpanded.personal
                        ? "Show fewer"
                        : `...and ${hiddenItems.length - 4} more hidden titles`}
                    </button>
                  ) : null}
                </div>
              ) : (
                <p className="page-subnote">You have no hidden movies right now.</p>
              )}
            </div>
          </details>
        </section>

        {user?.role === "admin" ? (
          <section className="settings-card settings-card--wide">
            <details className="settings-disclosure" open={showDesktopControlCenter || undefined}>
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
                {hiddenLoading ? (
                  <p className="page-subnote">Loading globally hidden movies...</p>
                ) : globalHiddenItems.length > 0 ? (
                  <div className="hidden-movie-list">
                    {visibleGlobalHiddenItems.map((hiddenItem) => (
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
                            disabled={
                              restoringGlobalItemId === hiddenItem.id
                              || movingToPersonalItemId === hiddenItem.id
                              || pendingHiddenReconciliation?.itemId === hiddenItem.id
                            }
                            onClick={() => handleShowForEveryone(hiddenItem.id)}
                            type="button"
                          >
                            {restoringGlobalItemId === hiddenItem.id ? "Restoring..." : "Show again"}
                          </button>
                          <button
                            className="ghost-button ghost-button--inline ghost-button--subtle"
                            disabled={
                              movingToPersonalItemId === hiddenItem.id
                              || restoringGlobalItemId === hiddenItem.id
                              || pendingHiddenReconciliation?.itemId === hiddenItem.id
                            }
                            onClick={() => handleHideForMe(hiddenItem)}
                            type="button"
                          >
                            {movingToPersonalItemId === hiddenItem.id ? "Hiding for me..." : "Hide for me"}
                          </button>
                        </div>
                      </article>
                    ))}
                    {showDesktopControlCenter && globalHiddenItems.length > 4 ? (
                      <button
                        className="control-center-list-expander"
                        onClick={() => setHiddenListsExpanded((current) => ({
                          ...current,
                          global: !current.global,
                        }))}
                        type="button"
                      >
                        {hiddenListsExpanded.global
                          ? "Show fewer"
                          : `...and ${globalHiddenItems.length - 4} more hidden titles`}
                      </button>
                    ) : null}
                  </div>
                ) : (
                  <p className="page-subnote">No globally hidden movies right now.</p>
                )}
              </div>
            </details>
          </section>
        ) : null}
        </div>
      </div>
      ) : null}

      {activeSettingsSection === "advanced" ? (
      <div
        aria-labelledby="settings-tab-advanced"
        className="settings-grid"
        id="settings-panel-advanced"
        role="tabpanel"
      >
        <section className="settings-card settings-card--wide">
          <h2>Poster display quality</h2>
          {loading ? (
            <p className="page-subnote">Loading poster display quality...</p>
          ) : (
            <label className="settings-field">
              <span>
                <strong>Maximum poster width</strong>
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
          )}
        </section>

        {totpStatus?.setup_available && !totpStatus?.enabled ? (
          <SettingsAccordionSection
            className="control-center-settings-totp"
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
          <div className="settings-grid__full-row" id="google-drive-oauth-setup">
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
                    autoComplete="off"
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
                  <NonLoginSecretInput
                    className="cloud-source-form__input"
                    disabled={googleDriveSetupSaving}
                    onChange={(event) =>
                      setGoogleDriveSetupDraft((current) => ({ ...current, client_secret: event.target.value }))
                    }
                    placeholder={googleDriveSetup.client_secret_configured
                      ? "Leave blank to keep the saved secret"
                      : "Enter the Google OAuth client secret"}
                    purpose="google-oauth-client-secret"
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
                <div className="control-center-destructive-actions">
                  <button
                    className="ghost-button ghost-button--inline"
                    disabled={googleDriveSetupSaving}
                    onClick={() => setGoogleDestructiveAction("clear-setup")}
                    type="button"
                  >
                    Clear saved OAuth overrides
                  </button>
                  <button
                    className="ghost-button ghost-button--inline ghost-button--danger"
                    disabled={googleDriveSetupSaving || !googleDriveSetup.connected}
                    onClick={() => setGoogleDestructiveAction("disconnect-account")}
                    type="button"
                  >
                    Disconnect Google account
                  </button>
                </div>
                {googleDestructiveAction ? (
                  <div className="control-center-confirm" role="alertdialog" aria-modal="true">
                    <strong>
                      {googleDestructiveAction === "clear-setup"
                        ? "Clear saved OAuth overrides?"
                        : "Disconnect this Google account?"}
                    </strong>
                    <p>
                      {googleDestructiveAction === "clear-setup"
                        ? "Database overrides will be removed. Connected account tokens and cloud sources will be kept."
                        : "Account tokens will be removed. Cloud source records and OAuth setup will be kept."}
                    </p>
                    <div className="player-actions">
                      <button
                        className="ghost-button ghost-button--inline"
                        disabled={googleDriveSetupSaving}
                        onClick={() => setGoogleDestructiveAction("")}
                        type="button"
                      >
                        Cancel
                      </button>
                      <button
                        className="ghost-button ghost-button--inline ghost-button--danger"
                        disabled={googleDriveSetupSaving}
                        onClick={handleGoogleDestructiveAction}
                        type="button"
                      >
                        {googleDriveSetupSaving ? "Working..." : "Confirm"}
                      </button>
                    </div>
                  </div>
                ) : null}
              </form>
            </div>
          </SettingsAccordionSection>
          </div>
        ) : null}

        {user?.role === "admin" ? (
          <SettingsAccordionSection
            description="Choose parent folders where Elvern should scan for media folders. Poster folders stay configured separately below."
            isOpen={openSections.mediaLibraryReference}
            onToggle={() => toggleSection("mediaLibraryReference")}
            title="Library reference locations"
          >
            <form className="admin-form" onSubmit={handleSharedMediaLibraryReferenceSave}>
              <label>
                Reference locations
                <div className="settings-path-picker__row">
                  <textarea
                    autoCapitalize="off"
                    autoComplete="off"
                    autoCorrect="off"
                    disabled={loading || sharedMediaLibraryReferenceSaving}
                    onChange={(event) => setSharedMediaLibraryReferenceInput(event.target.value)}
                    placeholder={sharedMediaLibraryReference.default_value || ""}
                    rows={Math.max(2, Math.min(5, String(sharedMediaLibraryReferenceInput || "").split(/\r?\n/).length))}
                    spellCheck="false"
                    value={sharedMediaLibraryReferenceInput}
                  />
                  <button
                    aria-label="Browse library reference directories on the Elvern host"
                    className="ghost-button ghost-button--inline settings-path-picker__button"
                    disabled={
                      loading
                      || sharedMediaLibraryReferenceSaving
                      || (directoryPicker.loading && directoryPicker.target === "shared-library")
                      || nativePickerPendingTarget === "shared-library"
                    }
                    onClick={() => handleOpenDirectoryPicker("shared-library")}
                    title="Browse library reference directories on the Elvern host"
                    type="button"
                  >
                    <span aria-hidden="true">📁</span>
                  </button>
                </div>
              </label>
              <StatusRow label="Active locations" value={sharedMediaLibraryReference.effective_value || "Unknown"} />
              <StatusRow label="Default location" value={sharedMediaLibraryReference.default_value || "Unknown"} />
              <LibraryReferenceCategorySummary summary={sharedMediaLibraryReference.category_summary} />
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
                  {sharedMediaLibraryReferenceSaving ? "Saving..." : "Save reference locations"}
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
