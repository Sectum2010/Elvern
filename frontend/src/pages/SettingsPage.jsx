import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { apiRequest } from "../lib/api";

const USER_SETTINGS_CHANGED_EVENT = "elvern:user-settings-changed";


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


export function SettingsPage() {
  const { user } = useAuth();
  const location = useLocation();
  const [settings, setSettings] = useState({
    hide_duplicate_movies: true,
    hide_recently_added: false,
    floating_controls_position: "bottom",
    media_library_reference_private_value: null,
    media_library_reference_shared_default_value: "",
    media_library_reference_effective_value: "",
  });
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
      account_email: null,
      account_name: null,
    },
    my_libraries: [],
    shared_libraries: [],
  });
  const [cloudBusyKey, setCloudBusyKey] = useState("");
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
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [openSections, setOpenSections] = useState({
    myLibraries: false,
    sharedLibraries: false,
    googleDriveSetup: false,
    mediaLibraryReference: false,
    posterReference: false,
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
        ]);
        if (active) {
          setSettings(settingsPayload);
          setHiddenItems(hiddenPayload.items || []);
          setGlobalHiddenItems(globalHiddenPayload.items || []);
          setCloudLibraries(cloudPayload);
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
    } else {
    }
  }, [user?.role]);

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
      await refreshCloudLibraries();
      setMessage(
        payload.configuration_state === "ready"
          ? "Google Drive setup saved. You can connect Google Drive below."
          : "Google Drive setup saved.",
      );
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
      const payload = await apiRequest("/api/cloud-libraries/google/connect", {
        method: "POST",
      });
      window.location.assign(payload.authorization_url);
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
        setMessage("Shared library added from Google Drive.");
      } else {
        setMyLibraryDraft({ resource_type: "folder", resource_id: "" });
        setMessage("Google Drive library added.");
      }
      await refreshCloudLibraries();
    } catch (requestError) {
      if (
        typeof window !== "undefined"
        && requestError?.status === 409
        && /already been added by your admin/i.test(requestError.message || "")
      ) {
        window.alert(requestError.message);
      }
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

      <div className="section-header">
        <div>
          <p className="eyebrow">Settings</p>
          <h1>Account and library preferences</h1>
        </div>
      </div>

      {error ? <p className="form-error">{error}</p> : null}
      {message ? <p className="page-note">{message}</p> : null}

      <div className="settings-grid">
        <section className="settings-card">
          <h2>Your account</h2>
          <StatusRow label="Username" value={user?.username || "Unknown"} />
          <StatusRow label="Session" value={user?.session_id ? `#${user.session_id}` : "Active"} />
          <p className="page-subnote">
            Password changes are admin-managed. Contact an admin if you need a password reset.
          </p>
        </section>

        <section className="settings-card">
          <h2>Library</h2>
          {loading ? (
            <p className="page-subnote">Loading your library preferences...</p>
          ) : (
            <>
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
            </>
          )}
        </section>

        <section className="settings-card">
          <h2>Interface</h2>
          {loading ? (
            <p className="page-subnote">Loading interface preferences...</p>
          ) : (
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
          )}
        </section>

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
                    {cloudLibraries.google.connected
                      ? `Connected as ${cloudLibraries.google.account_name || cloudLibraries.google.account_email || "Google account"}`
                      : "Connect your Google account to add Drive folders or shared drives."}
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
                    : cloudLibraries.google.connected
                      ? "Reconnect Google Drive"
                      : "Connect Google Drive"}
                </button>
              </div>

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
                        {source.last_error ? <p className="form-error">{source.last_error}</p> : null}
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
                      {source.last_error ? <p className="form-error">{source.last_error}</p> : null}
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
            badge={googleDriveSetup.configuration_label}
            description="Configure a real HTTPS Google OAuth origin for this Elvern server here. Once saved, your My Libraries and Shared Libraries sections can connect to Google Drive without editing env files manually."
            isOpen={openSections.googleDriveSetup}
            onToggle={() => toggleSection("googleDriveSetup")}
            title="Google Drive Setup"
          >
            <div className="cloud-libraries-stack">
              <div className="cloud-connection-card google-drive-setup-card">
                <div className="cloud-connection-card__copy">
                  <strong>Configuration</strong>
                  <small>{googleDriveSetup.status_message}</small>
                </div>
                <div className="google-drive-setup-status-grid">
                  <StatusRow label="State" value={googleDriveSetup.configuration_label} />
                  <StatusRow
                    label="Connection"
                    value={
                      googleDriveSetup.connected
                        ? `Connected${googleDriveSetup.account_name || googleDriveSetup.account_email ? ` as ${googleDriveSetup.account_name || googleDriveSetup.account_email}` : ""}`
                        : "Not connected"
                    }
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
              </div>

              <form className="cloud-source-form" onSubmit={handleGoogleDriveSetupSave}>
                <label className="settings-field">
                  <span>
                    <strong>HTTPS app origin</strong>
                    <small>Use the private HTTPS hostname users actually browse to, not a raw HTTP IP address.</small>
                  </span>
                  <input
                    autoCapitalize="off"
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
                    type="password"
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
                    placeholder={sharedMediaLibraryReference.default_value || "/srv/media/movies"}
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
              <StatusRow label="Using now" value={sharedMediaLibraryReference.effective_value || "Unknown"} />
              <StatusRow label="Bootstrap path" value={sharedMediaLibraryReference.default_value || "Unknown"} />
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
                    placeholder={posterReference.default_value || "/path/to/Posters"}
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
              <StatusRow label="Effective location" value={posterReference.effective_value || "Unknown"} />
              <StatusRow label="Default location" value={posterReference.default_value || "Unknown"} />
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

        {user?.role === "admin" ? (
          <section className="settings-card settings-card--wide">
            <div className="settings-inline-header">
              <div>
                <h2>Admin tools</h2>
                <p className="page-subnote">Manage users, password resets, sessions, audit logs, and rescans.</p>
              </div>
              <Link className="ghost-button ghost-button--inline" to="/admin">
                Open Admin
              </Link>
            </div>
          </section>
        ) : null}
      </div>
    </section>
  );
}
