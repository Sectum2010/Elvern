import {
  Check,
  ChevronDown,
  Cloud,
  Copy,
  FolderOpen,
  RefreshCw,
} from "lucide-react";
import { useState } from "react";

import {
  BACKGROUND_PRESETS,
  buildBackgroundPreviewStyle,
} from "../../lib/userBackground.js";

function MeridianCard({ children, className = "", ...props }) {
  return <section className={`meridian-card${className ? ` ${className}` : ""}`} {...props}>{children}</section>;
}

function Segmented({ ariaLabel, disabled = false, onChange, options, value }) {
  return (
    <div aria-label={ariaLabel} className="meridian-segmented" role="radiogroup">
      {options.map((option) => (
        <button
          aria-checked={option.value === value}
          className={option.value === value ? "meridian-segmented__option meridian-segmented__option--active" : "meridian-segmented__option"}
          disabled={disabled}
          key={option.value}
          onClick={() => onChange(option.value)}
          role="radio"
          type="button"
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function ToggleRow({ checked, description, disabled, label, onChange }) {
  return (
    <label className="meridian-toggle-row">
      <span className="meridian-row-copy">
        <strong>{label}</strong>
        <small>{description}</small>
      </span>
      <input checked={checked} disabled={disabled} onChange={onChange} type="checkbox" />
      <span aria-hidden="true" className="meridian-switch"><i /></span>
    </label>
  );
}

function PillButton({ children, className = "", ...props }) {
  return <button className={`meridian-pill-button${className ? ` ${className}` : ""}`} type="button" {...props}>{children}</button>;
}

function StatusPill({ children, tone = "neutral" }) {
  return <span className={`meridian-status-pill meridian-status-pill--${tone}`}>{children}</span>;
}

function SettingsFeedback({ error, message }) {
  const text = error || message || "";
  if (!text) return null;
  return (
    <div
      aria-live="polite"
      className={`meridian-toast${error ? " meridian-toast--error" : ""}`}
      role={error ? "alert" : "status"}
    >
      {text}
    </div>
  );
}

function SettingsResourceErrors({ errors = [], onRetry }) {
  if (!errors.length) return null;
  return (
    <div className="meridian-resource-errors">
      {errors.map((resourceError) => (
        <div className="meridian-resource-error" key={resourceError.key} role="alert">
          <span>{resourceError.message}</span>
          <PillButton onClick={() => onRetry?.(resourceError.key)}>Retry</PillButton>
        </div>
      ))}
    </div>
  );
}


function getCloudConnectionPresentation(google) {
  if (!google?.enabled) {
    return { label: "Not configured", message: "Google Drive OAuth is not configured.", tone: "neutral" };
  }
  if (google.reconnect_required) {
    return { label: "Reconnect required", message: "Reconnect Google Drive to continue cloud access.", tone: "warning" };
  }
  if (google.connection_status === "error") {
    return { label: "Error", message: google.status_message || "Google Drive could not be reached.", tone: "danger" };
  }
  if (google.connected) {
    const accountLabel = google.account_name || google.account_email || "Google account";
    return { label: "Connected", message: `Connected as ${accountLabel}.`, tone: "success" };
  }
  return { label: "Not connected", message: "Connect Google Drive to add cloud libraries.", tone: "neutral" };
}

function AppearancePanel({ model }) {
  const {
    backgroundDraft,
    backgroundError,
    backgroundSaving,
    loading,
    saving,
    settings,
  } = model;
  const modeOptions = [
    { value: "preset", label: "Presets" },
    { value: "gradient", label: "Gradient" },
    { value: "solid", label: "Solid" },
    { value: "photo", label: "Photo" },
  ];
  return (
    <div className="meridian-panel-stack">
      <MeridianCard>
        <div className="meridian-setting-row">
          <span className="meridian-row-copy">
            <strong>Poster appearance</strong>
            <small>How movie cards appear in your library.</small>
          </span>
          <Segmented
            ariaLabel="Poster appearance"
            disabled={loading || saving}
            onChange={model.onPosterAppearanceChange}
            options={model.posterAppearanceOptions}
            value={settings.poster_card_appearance}
          />
        </div>
        <div className="meridian-divider" />
        <div className="meridian-setting-row">
          <span className="meridian-row-copy">
            <strong>Floating island position</strong>
            <small>Where the control island docks in the library.</small>
          </span>
          <Segmented
            ariaLabel="Desktop Floating Island position"
            disabled={loading || saving}
            onChange={model.onIslandPositionChange}
            options={[{ value: "top", label: "Top" }, { value: "bottom", label: "Bottom" }]}
            value={settings.desktop_floating_island_position === "bottom" ? "bottom" : "top"}
          />
        </div>
        <div className="meridian-divider" />
        <div className="meridian-setting-row">
          <span className="meridian-row-copy">
            <strong>Maximum poster width</strong>
            <small>Largest image size used for library card posters.</small>
          </span>
          <div className="meridian-chip-row" role="radiogroup" aria-label="Maximum poster width">
            {model.posterWidthOptions.map((option) => (
              <button
                aria-checked={settings.poster_card_display_max_width === option.value}
                className={settings.poster_card_display_max_width === option.value ? "meridian-chip meridian-chip--active" : "meridian-chip"}
                disabled={loading || saving}
                key={option.value}
                onClick={() => model.onPosterWidthChange({ target: { value: option.value } })}
                role="radio"
                type="button"
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </MeridianCard>

      <MeridianCard>
        <div className="meridian-card-header">
          <h2>Background</h2>
          <Segmented
            ariaLabel="Background mode"
            disabled={backgroundSaving}
            onChange={model.onBackgroundModeChange}
            options={modeOptions}
            value={backgroundDraft.background_mode}
          />
        </div>
        {backgroundDraft.background_mode === "preset" ? (
          <div aria-label="Background presets" className="meridian-preset-grid" role="radiogroup">
            {BACKGROUND_PRESETS.map((preset) => {
              const selected = backgroundDraft.background_preset === preset.value;
              return (
                <button
                  aria-checked={selected}
                  className={selected ? "meridian-preset meridian-preset--active" : "meridian-preset"}
                  disabled={backgroundSaving}
                  key={preset.value}
                  onClick={() => model.onBackgroundPresetSelect(preset.value)}
                  role="radio"
                  type="button"
                >
                  <i aria-hidden="true" style={{ background: preset.swatch }} />
                  <span>{preset.label}</span>
                  {selected ? <Check aria-hidden="true" size={14} /> : null}
                </button>
              );
            })}
          </div>
        ) : null}
        {backgroundDraft.background_mode === "gradient" ? (
          <div className="meridian-background-editor">
            <div className="meridian-background-preview" style={buildBackgroundPreviewStyle({ ...backgroundDraft, background_custom_model: "hue_v2" })} />
            <div className="meridian-slider-grid">
              <label><span>START HUE</span><input aria-label="Start hue" max="359" min="0" onChange={(event) => model.onBackgroundDraftChange("background_gradient_start_hue", Number(event.target.value))} type="range" value={backgroundDraft.background_gradient_start_hue} /></label>
              <label><span>END HUE</span><input aria-label="End hue" max="359" min="0" onChange={(event) => model.onBackgroundDraftChange("background_gradient_end_hue", Number(event.target.value))} type="range" value={backgroundDraft.background_gradient_end_hue} /></label>
            </div>
            <div className="meridian-actions"><PillButton className="meridian-pill-button--primary" disabled={backgroundSaving} onClick={model.onHueSave}>Save gradient</PillButton><PillButton disabled={backgroundSaving} onClick={model.onHueReset}>Reset</PillButton></div>
          </div>
        ) : null}
        {backgroundDraft.background_mode === "solid" ? (
          <div className="meridian-background-editor">
            <div className="meridian-background-preview" style={buildBackgroundPreviewStyle({ ...backgroundDraft, background_custom_model: "hue_v2" })} />
            <div className="meridian-slider-grid meridian-slider-grid--single">
              <label><span>HUE</span><input aria-label="Hue" max="359" min="0" onChange={(event) => model.onBackgroundDraftChange("background_solid_hue", Number(event.target.value))} type="range" value={backgroundDraft.background_solid_hue} /></label>
            </div>
            <div className="meridian-actions"><PillButton className="meridian-pill-button--primary" disabled={backgroundSaving} onClick={model.onHueSave}>Save solid</PillButton><PillButton disabled={backgroundSaving} onClick={model.onHueReset}>Reset</PillButton></div>
          </div>
        ) : null}
        {backgroundDraft.background_mode === "photo" ? (
          <div className="meridian-background-editor">
            <div
              className="meridian-background-preview meridian-background-preview--photo"
              style={settings.background_photo_url ? buildBackgroundPreviewStyle({ ...backgroundDraft, background_mode: "photo", background_photo_url: settings.background_photo_url }) : undefined}
            >
              <span>{settings.background_photo_original_filename || "Background photo"}</span>
            </div>
            <div className="meridian-actions">
              <label className="meridian-pill-button meridian-pill-button--primary meridian-upload">
                <span>{settings.background_photo_url ? "Replace photo" : "Upload photo"}</span>
                <input accept="image/jpeg,image/png,image/webp" disabled={backgroundSaving} onChange={model.onPhotoUpload} type="file" />
              </label>
              <PillButton disabled={backgroundSaving} onClick={model.onBackgroundResetRequest}>Reset</PillButton>
            </div>
          </div>
        ) : null}
        {backgroundError ? <p className="meridian-inline-error" role="alert">{backgroundError}</p> : null}
      </MeridianCard>
    </div>
  );
}

function LibraryPanel({ model }) {
  return (
    <div className="meridian-panel-stack">
      <MeridianCard>
        <ToggleRow checked={model.settings.hide_recently_added} description="Remove the Recently added section from your Library view." disabled={model.saving} label="Hide “Recently added”" onChange={model.onRecentlyAddedToggle} />
        <div className="meridian-divider" />
        <ToggleRow checked={model.settings.hide_duplicate_movies} description="Show only the highest-quality copy for the same title, year, and edition." disabled={model.saving} label="Hide duplicate copies" onChange={model.onDuplicateToggle} />
      </MeridianCard>
      <MeridianCard className={model.isAdmin && !model.ageBuckets.length ? "meridian-age-card meridian-age-card--empty" : "meridian-age-card"}>
        <div className="meridian-card-header">
          <span className="meridian-row-copy"><strong>Age restrictions</strong><small>Review automatic movie age groups and explicit manual links.</small></span>
          {model.isAdmin ? <PillButton className="meridian-age-refresh" disabled={model.ageGroupsLoading} onClick={model.onRefreshAgeGroups}><RefreshCw aria-hidden="true" className={model.ageGroupsLoading ? "is-spinning" : ""} size={14} />{model.ageGroupsLoading ? "Refreshing…" : "Refresh"}</PillButton> : null}
        </div>
        {model.isAdmin && model.ageBuckets.length ? (
          <div className="meridian-age-list">
            {model.ageBuckets.map((bucket) => (
              <button key={bucket.age} onClick={() => model.onOpenAgeBucket(bucket)} type="button">
                <StatusPill>{bucket.ageLabel}</StatusPill>
                <span><strong>{bucket.groupCount} movie group{bucket.groupCount === 1 ? "" : "s"}</strong><small>{bucket.copiesCount} copies · {bucket.manualLinksCount} manual links</small></span>
                <span>Manage</span>
              </button>
            ))}
          </div>
        ) : (
          <div className="meridian-empty-state"><strong>No age-restricted movies yet.</strong><span>Set an age requirement from a movie's Info panel.</span></div>
        )}
      </MeridianCard>
    </div>
  );
}

function CloudPanel({ model }) {
  const [destination, setDestination] = useState("personal");
  const draft = destination === "shared" ? model.sharedLibraryDraft : model.myLibraryDraft;
  const setDraft = destination === "shared" ? model.setSharedLibraryDraft : model.setMyLibraryDraft;
  const google = model.cloudLibraries.google || {};
  const connection = getCloudConnectionPresentation(google);
  const connected = Boolean(google.connected && !google.reconnect_required);
  const operationPending = Boolean(model.reconnectPending);
  const addDisabled = !connected
    || operationPending
    || model.cloudBusyKey.startsWith("add-")
    || !String(draft.resource_id || "").trim();
  const sources = [
    ...(model.cloudLibraries.my_libraries || []).map((source) => ({ ...source, meridianScope: "Personal" })),
    ...(model.cloudLibraries.shared_libraries || []).map((source) => ({ ...source, meridianScope: "Everyone" })),
  ];
  return (
    <div className="meridian-panel-stack">
      <MeridianCard className="meridian-connection-card">
        <span className="meridian-icon-tile"><Cloud aria-hidden="true" size={19} /></span>
        <span className="meridian-row-copy">
          <strong>Google Drive</strong>
          <small>{connection.message}</small>
        </span>
        <StatusPill tone={connection.tone}>{connection.label}</StatusPill>
        <PillButton disabled={operationPending} onClick={model.onGoogleConnect}>{operationPending ? "Connecting…" : connected || google.reconnect_required ? "Reconnect" : "Connect"}</PillButton>
      </MeridianCard>
      {model.authTransaction?.state === "account_mismatch" ? (
        <MeridianCard aria-labelledby="google-account-mismatch-title" className="meridian-account-mismatch" role="alertdialog">
          <span className="meridian-row-copy">
            <strong id="google-account-mismatch-title">Use a different Google account?</strong>
            <small>
              Existing cloud sources belong to {model.authTransaction.candidate?.current_account_label || "the previous Google account"}.
              {" "}Replace it with {model.authTransaction.candidate?.candidate_account_label || "the newly selected account"} only after Elvern verifies each source.
            </small>
          </span>
          {model.authTransaction.message ? <p className="meridian-inline-error">{model.authTransaction.message}</p> : null}
          <div className="meridian-actions">
            <PillButton disabled={operationPending} onClick={model.onAccountReplacementCancel}>Cancel</PillButton>
            <PillButton className="meridian-pill-button--primary" disabled={operationPending} onClick={model.onAccountReplacementConfirm}>{operationPending ? "Verifying…" : "Replace account"}</PillButton>
          </div>
        </MeridianCard>
      ) : null}
      <MeridianCard className="meridian-cloud-add-card">
        <div className="meridian-row-copy"><strong>Add a cloud library</strong><small>Personal libraries appear only in your Library; shared libraries appear for every user.</small></div>
        <div className="meridian-cloud-form">
          <label><span>DESTINATION</span><Segmented ariaLabel="Cloud library destination" onChange={setDestination} options={model.isAdmin ? [{ value: "personal", label: "Personal" }, { value: "shared", label: "Everyone" }] : [{ value: "personal", label: "Personal" }]} value={destination} /></label>
          <label><span>RESOURCE TYPE</span><Segmented ariaLabel="Cloud resource type" onChange={(value) => setDraft((current) => ({ ...current, resource_type: value }))} options={[{ value: "folder", label: "Folder" }, { value: "shared_drive", label: "Shared drive" }]} value={draft.resource_type} /></label>
          <label className="meridian-cloud-form__id"><span>GOOGLE DRIVE RESOURCE ID</span><span><input disabled={!connected || operationPending} onChange={(event) => setDraft((current) => ({ ...current, resource_id: event.target.value }))} placeholder="Paste the folder or shared drive ID" value={draft.resource_id} /><button disabled={addDisabled} onClick={() => model.onAddCloudSource(destination)} type="button">Add</button></span></label>
        </div>
      </MeridianCard>
      <MeridianCard>
        <strong>Cloud libraries</strong>
        <div className="meridian-source-list">
          {sources.map((source) => (
            <article className={source.hidden_for_user ? "meridian-source-row meridian-source-row--hidden" : "meridian-source-row"} key={source.id}>
              <span className="meridian-source-row__initial">{String(source.display_name || "C").charAt(0).toUpperCase()}</span>
              <span className="meridian-row-copy"><strong>{source.display_name || "Cloud library"}</strong><small>{source.item_count || 0} item(s) · {source.resource_type === "shared_drive" ? "Shared drive" : "Folder"} · Cloud · Last synced {model.formatCloudTimestamp(source.last_synced_at)}</small></span>
              <StatusPill tone={source.meridianScope === "Everyone" ? "accent" : "neutral"}>{source.meridianScope === "Everyone" ? `Shared${source.owner_username ? ` by ${source.owner_username}` : ""}` : "Personal"}</StatusPill>
              {model.isAdmin && source.meridianScope === "Personal" ? <PillButton disabled={model.cloudBusyKey === `share-globally-${source.id}`} onClick={() => model.onMoveCloudSource(source, true)}>{model.cloudBusyKey === `share-globally-${source.id}` ? "Sharing…" : "Share globally"}</PillButton> : null}
              {model.isAdmin && source.meridianScope === "Everyone" && source.owner_username === model.username ? <PillButton disabled={model.cloudBusyKey === `move-to-my-${source.id}`} onClick={() => model.onMoveCloudSource(source, false)}>{model.cloudBusyKey === `move-to-my-${source.id}` ? "Moving…" : "Move to My Libraries"}</PillButton> : null}
              {source.meridianScope === "Everyone" ? <PillButton disabled={model.cloudBusyKey === `shared-visibility-${source.id}`} onClick={() => model.onSharedVisibilityToggle(source)}>{model.cloudBusyKey === `shared-visibility-${source.id}` ? (source.hidden_for_user ? "Showing…" : "Hiding…") : source.hidden_for_user ? "Show in Library" : "Hide for me"}</PillButton> : null}
            </article>
          ))}
          {!(model.cloudLibraries.my_libraries || []).length ? <p className="meridian-muted-copy">No personal cloud libraries added yet.</p> : null}
        </div>
      </MeridianCard>
    </div>
  );
}

function HiddenPanel({ model }) {
  const [scope, setScope] = useState("personal");
  const personal = scope === "personal" || !model.isAdmin;
  const items = personal ? model.hiddenItems : model.globalHiddenItems;
  const expanded = personal ? model.hiddenExpanded.personal : model.hiddenExpanded.global;
  const visible = expanded ? items : items.slice(0, 4);
  const status = model.hiddenStatus || { error: "", loaded: false, loading: false };
  const initialLoading = status.loading && !status.loaded;
  const refreshingWithData = status.loading && status.loaded;
  return (
    <div className="meridian-panel-stack">
      <Segmented ariaLabel="Hidden title scope" onChange={setScope} options={model.isAdmin ? [{ value: "personal", label: `For me (${model.hiddenItems.length})` }, { value: "global", label: `For everyone (${model.globalHiddenItems.length})` }] : [{ value: "personal", label: `For me (${model.hiddenItems.length})` }]} value={scope} />
      <MeridianCard className="meridian-hidden-card" id="hidden-list">
        {initialLoading ? <div aria-label="Loading hidden titles" className="meridian-hidden-skeleton"><i /><i /><i /></div> : null}
        {refreshingWithData ? <span className="meridian-resource-refresh" role="status">Refreshing…</span> : null}
        {status.error ? <div className="meridian-resource-error" role="alert"><span>{status.error}</span><PillButton onClick={model.onRetry}>Retry</PillButton></div> : null}
        {status.loaded && !items.length ? <div className="meridian-empty-state"><strong>You have no hidden movies right now.</strong><span>These items stay out of your library until you restore them or change their hidden scope.</span></div> : null}
        {visible.map((item) => (
          <article className="meridian-hidden-row" key={item.id}>
            <span className="meridian-hidden-row__initial">{String(item.title || "E").trim().charAt(0).toUpperCase() || "E"}</span>
            <span className="meridian-row-copy">
              <strong>{item.title}</strong>
              {item.year || item.edition_label ? <small>{[item.year, item.edition_label].filter(Boolean).join(" · ")}</small> : null}
            </span>
            <PillButton
              className="meridian-pill-button--primary"
              disabled={(personal ? model.restoringItemId : model.restoringGlobalItemId) === item.id || model.pendingReconciliationItemId === item.id}
              onClick={() => (personal ? model.onShowAgain(item.id) : model.onShowForEveryone(item.id))}
            >
              {(personal ? model.restoringItemId : model.restoringGlobalItemId) === item.id ? "Restoring…" : "Show again"}
            </PillButton>
            {personal && model.isAdmin ? <PillButton disabled={model.movingToGlobalItemId === item.id || model.pendingReconciliationItemId === item.id} onClick={() => model.onHideForEveryone(item)}>{model.movingToGlobalItemId === item.id ? "Hiding…" : "Hide for everyone"}</PillButton> : null}
            {!personal ? <PillButton disabled={model.movingToPersonalItemId === item.id || model.pendingReconciliationItemId === item.id} onClick={() => model.onHideForMe(item)}>{model.movingToPersonalItemId === item.id ? "Hiding…" : "Hide for me"}</PillButton> : null}
          </article>
        ))}
        {items.length > 4 ? (
          <button className="meridian-list-expander" onClick={() => model.onHiddenExpandedChange(scope, !expanded)} type="button">
            {expanded ? "Show less" : `…and ${items.length - 4} more hidden titles`}
            <ChevronDown aria-hidden="true" className={expanded ? "is-expanded" : ""} size={14} />
          </button>
        ) : null}
      </MeridianCard>
    </div>
  );
}

function ResourceCardState({ label, onRetry, status, variant }) {
  if (status?.error && !status.loaded) {
    return (
      <MeridianCard className="meridian-resource-state">
        <div className="meridian-resource-error" role="alert"><span>{status.error}</span><PillButton onClick={onRetry}>Retry</PillButton></div>
      </MeridianCard>
    );
  }
  return (
    <MeridianCard
      aria-label={`Loading ${label}`}
      className={`meridian-resource-skeleton meridian-resource-skeleton--${variant}`}
    >
      <i /><i /><i />
    </MeridianCard>
  );
}

function deriveGoogleRegistrationValues(draft) {
  const candidate = String(draft?.https_origin || "").trim().replace(/\/+$/, "");
  try {
    const parsed = new URL(candidate);
    const rawIpAddress = /^(?:\d{1,3}\.){3}\d{1,3}$/.test(parsed.hostname)
      || parsed.hostname.includes(":");
    if (
      parsed.protocol !== "https:"
      || parsed.pathname !== "/"
      || parsed.search
      || parsed.hash
      || parsed.username
      || parsed.password
      || rawIpAddress
    ) {
      return { origin: "", redirectUri: "" };
    }
    const origin = parsed.origin;
    return { origin, redirectUri: `${origin}/api/cloud-libraries/google/callback` };
  } catch {
    return { origin: "", redirectUri: "" };
  }
}

function ServerPanel({ model }) {
  const [step, setStep] = useState(0);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [posterRulesOpen, setPosterRulesOpen] = useState(false);
  const steps = ["1 · Origin", "2 · Credentials", "3 · Register", "4 · Connect"];
  const registration = deriveGoogleRegistrationValues(model.googleSetupDraft);
  const originReady = Boolean(registration.origin);
  const credentialsReady = Boolean(
    String(model.googleSetupDraft.client_id || "").trim()
    && (String(model.googleSetupDraft.client_secret || "").trim() || model.googleSetup.client_secret_configured),
  );
  const maxAccessibleStep = !originReady ? 0 : !credentialsReady ? 1 : 3;
  const categoryRows = [
    ["Movies stored under", model.sharedReference.category_summary?.movies || []],
    ["TV stored under", model.sharedReference.category_summary?.tv || []],
    ["Cartoon stored under", model.sharedReference.category_summary?.cartoon || []],
    ["Anime stored under", model.sharedReference.category_summary?.anime || []],
  ];
  const readyStatus = { error: "", loaded: true, loading: false };
  const googleStatus = model.resourceStatus?.googleSetup || readyStatus;
  const mediaStatus = model.resourceStatus?.mediaReference || readyStatus;
  const posterStatus = model.resourceStatus?.posterReference || readyStatus;
  const canContinue = step === 0 ? originReady : step === 1 ? credentialsReady : true;
  return (
    <div className="meridian-panel-stack">
      {!googleStatus.loaded ? <ResourceCardState label="Google Drive OAuth setup" onRetry={model.onRetryGoogleSetup} status={googleStatus} variant="oauth" /> : <MeridianCard className="meridian-oauth-card" id="google-drive-oauth-setup">
        <div className="meridian-card-header"><span className="meridian-row-copy"><strong>Google Drive OAuth Setup</strong><small>Configure the secure OAuth connection used by cloud libraries.</small></span><StatusPill tone={model.googleSetup.configuration_state === "ready" ? "success" : "neutral"}>{model.googleSetupBadgeLabel}</StatusPill></div>
        {googleStatus.error ? <div className="meridian-resource-error" role="alert"><span>{googleStatus.error}</span><PillButton onClick={model.onRetryGoogleSetup}>Retry</PillButton></div> : null}
        <div className="meridian-stepper">
          {steps.map((label, index) => <button className={index === step ? "is-active" : index < step ? "is-complete" : ""} disabled={index > maxAccessibleStep} key={label} onClick={() => setStep(index)} type="button"><i /><span>{label}</span></button>)}
        </div>
        <form className="meridian-oauth-form" onSubmit={model.onGoogleSetupSave}>
          <div className="meridian-oauth-step-content">
          {step === 0 ? <label><span>HTTPS APP ORIGIN</span><input onChange={(event) => model.setGoogleSetupDraft((current) => ({ ...current, https_origin: event.target.value }))} placeholder="https://elvern.example.com" value={model.googleSetupDraft.https_origin} /><small>Use the private HTTPS hostname users actually browse to, not a raw HTTP IP address.</small></label> : null}
          {step === 1 ? <><label><span>GOOGLE OAUTH CLIENT ID</span><input onChange={(event) => model.setGoogleSetupDraft((current) => ({ ...current, client_id: event.target.value }))} value={model.googleSetupDraft.client_id} /></label><label><span>GOOGLE OAUTH CLIENT SECRET</span>{model.secretInput}</label></> : null}
          {step === 2 ? <div className="meridian-register-values"><span>AUTHORIZED JAVASCRIPT ORIGIN<strong>{registration.origin || "Set an HTTPS origin first."}</strong></span><span>AUTHORIZED REDIRECT URI<strong>{registration.redirectUri || "Available after the origin is configured."}</strong><PillButton disabled={!registration.redirectUri} onClick={() => model.onCopyGoogleCallback(registration.redirectUri)}><Copy aria-hidden="true" size={14} />Copy</PillButton></span></div> : null}
          {step === 3 ? <div className="meridian-oauth-status-grid">{[["OAuth setup", model.googleSetupBadgeLabel], ["Account health", model.googleConnectionHealth], ["Source health", model.sourceHealth], ["HTTPS origin", model.googleSetup.missing_fields?.includes("https_origin") ? "Missing" : "Configured"], ["Client ID", model.googleSetup.missing_fields?.includes("client_id") ? "Missing" : "Configured"], ["Client Secret", model.googleSetup.missing_fields?.includes("client_secret") ? "Missing" : "Configured"]].map(([key, value]) => <span key={key}><small>{key}</small><strong>{value}</strong></span>)}</div> : null}
          </div>
          <div className="meridian-step-actions"><PillButton disabled={step === 0} onClick={() => setStep((current) => Math.max(0, current - 1))}>Back</PillButton>{step < 3 ? <PillButton className="meridian-pill-button--primary" disabled={!canContinue} onClick={() => setStep((current) => Math.min(3, current + 1))}>Continue</PillButton> : <button className="meridian-pill-button meridian-pill-button--primary" disabled={model.googleSetupSaving} type="submit">{model.googleSetupSaving ? "Saving…" : "Save Google Drive Setup"}</button>}</div>
        </form>
      </MeridianCard>}

      {!mediaStatus.loaded ? <ResourceCardState label="library reference locations" onRetry={model.onRetryMediaReference} status={mediaStatus} variant="media-reference" /> : <MeridianCard className="meridian-reference-card">
        <div className="meridian-card-header"><span className="meridian-row-copy"><strong>Library reference locations</strong><small>Parent folders where Elvern scans for media. Poster folder is configured separately below.</small></span><button aria-label="Browse library reference directories" className="meridian-folder-button" onClick={() => model.onOpenDirectoryPicker("shared-library")} type="button"><FolderOpen aria-hidden="true" size={16} /></button></div>
        {mediaStatus.error ? <div className="meridian-resource-error" role="alert"><span>{mediaStatus.error}</span><PillButton onClick={model.onRetryMediaReference}>Retry</PillButton></div> : null}
        <textarea className="meridian-path-input" onChange={(event) => model.setSharedReferenceInput(event.target.value)} rows="3" value={model.sharedReferenceInput} />
        <div className="meridian-path-summary"><span>ACTIVE<strong>{model.sharedReference.effective_value || "None configured"}</strong></span><span>DEFAULT<strong>{model.sharedReference.default_value || "None configured"}</strong></span></div>
        <div className="meridian-reference-categories">
          {categoryRows.map(([label, entries]) => (
            <div key={label}>
              <strong>{label}</strong>
              <span>{entries.length ? entries.map((entry) => entry.name || entry.path).join(", ") : "None found"}</span>
            </div>
          ))}
        </div>
        <button aria-expanded={rulesOpen} className="meridian-rules-toggle" onClick={() => setRulesOpen((current) => !current)} type="button"><ChevronDown aria-hidden="true" className={rulesOpen ? "is-expanded" : ""} size={13} /><strong>Path rules</strong><span className="meridian-chip-row" aria-label="Supported media folder suffixes">{["-M", "-TV", "-AN", "-C", "-L", "-S", "-X"].map((suffix) => <span className="meridian-chip" key={suffix}>{suffix}</span>)}</span></button>
        {rulesOpen ? <div className="meridian-path-rules">{(model.sharedReference.validation_rules || []).map((rule) => <small className="meridian-rule-copy" key={rule}>· {rule}</small>)}</div> : null}
        <div className="meridian-actions"><button className="meridian-pill-button meridian-pill-button--primary" disabled={model.sharedReferenceSaving} onClick={model.onSharedReferenceSave} type="button">{model.sharedReferenceSaving ? "Saving…" : "Save reference locations"}</button></div>
      </MeridianCard>}

      {!posterStatus.loaded ? <ResourceCardState label="poster reference location" onRetry={model.onRetryPosterReference} status={posterStatus} variant="poster-reference" /> : <MeridianCard className="meridian-reference-card">
        <div className="meridian-card-header"><span className="meridian-row-copy"><strong>Poster reference location</strong><small>Global admin-only poster directory for every user. Leave at the Linux default unless Elvern must scan a different mounted folder.</small></span><button aria-label="Browse poster directories" className="meridian-folder-button" onClick={() => model.onOpenDirectoryPicker("poster-reference")} type="button"><FolderOpen aria-hidden="true" size={16} /></button></div>
        {posterStatus.error ? <div className="meridian-resource-error" role="alert"><span>{posterStatus.error}</span><PillButton onClick={model.onRetryPosterReference}>Retry</PillButton></div> : null}
        <input className="meridian-path-input" onChange={(event) => model.setPosterReferenceInput(event.target.value)} value={model.posterReferenceInput} />
        <div className="meridian-path-summary"><span>CURRENT<strong>{model.posterReference.effective_value || "None configured"}</strong></span><span>DEFAULT<strong>{model.posterReference.default_value || "None configured"}</strong></span></div>
        <button aria-expanded={posterRulesOpen} className="meridian-rules-toggle" onClick={() => setPosterRulesOpen((current) => !current)} type="button"><ChevronDown aria-hidden="true" className={posterRulesOpen ? "is-expanded" : ""} size={13} /><strong>Accepted paths</strong></button>
        {posterRulesOpen ? <div className="meridian-path-rules">{(model.posterReference.validation_rules || []).map((rule) => <small className="meridian-rule-copy" key={rule}>· {rule}</small>)}</div> : null}
        <div className="meridian-actions"><button className="meridian-pill-button meridian-pill-button--primary" disabled={model.posterReferenceSaving} onClick={model.onPosterReferenceSave} type="button">{model.posterReferenceSaving ? "Saving…" : "Save poster location"}</button></div>
      </MeridianCard>}
    </div>
  );
}

export function MeridianSettingsView({ model, tab }) {
  return (
    <div className="meridian-settings-view">
      <SettingsResourceErrors errors={model.resourceErrors} onRetry={model.onRetryResource} />
      {tab === "appearance" ? <AppearancePanel model={model.appearance} /> : null}
      {tab === "library" ? <LibraryPanel model={model.library} /> : null}
      {tab === "cloud-sharing" ? <CloudPanel model={model.cloud} /> : null}
      {tab === "hidden-titles" ? <HiddenPanel model={model.hidden} /> : null}
      {tab === "playback-apps" ? <div className="meridian-panel-stack meridian-playback-panel">{model.playbackPanel}</div> : null}
      {tab === "server-storage" ? <ServerPanel model={model.server} /> : null}
      <SettingsFeedback error={model.error} message={model.message} resourceErrors={model.resourceErrors} />
    </div>
  );
}
