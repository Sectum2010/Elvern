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

function SettingsFeedback({ error, message, resourceErrors = [] }) {
  const text = error || message || resourceErrors[0]?.message || "";
  if (!text) return null;
  return (
    <div
      aria-live="polite"
      className={`meridian-toast${error || resourceErrors.length ? " meridian-toast--error" : ""}`}
      role={error ? "alert" : "status"}
    >
      {text}
    </div>
  );
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
      <MeridianCard>
        <div className="meridian-card-header">
          <span className="meridian-row-copy"><strong>Age restrictions</strong><small>Review automatic movie age groups and explicit manual links.</small></span>
          {model.isAdmin ? <PillButton className="meridian-pill-button--primary" disabled={model.ageGroupsLoading} onClick={model.onRefreshAgeGroups}><RefreshCw aria-hidden="true" className={model.ageGroupsLoading ? "is-spinning" : ""} size={14} />{model.ageGroupsLoading ? "Refreshing…" : "Refresh"}</PillButton> : null}
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
  const connected = Boolean(model.cloudLibraries.google?.connected);
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
          <small>{model.cloudLibraries.google?.status_message || (connected ? `Connected as ${model.cloudLibraries.google.account_name || model.cloudLibraries.google.account_email || "Google account"}` : "Connect Google Drive to add cloud libraries.")}</small>
        </span>
        <StatusPill tone={connected ? "success" : "neutral"}>{connected ? "Connected" : model.cloudLibraries.google?.reconnect_required ? "Reconnect required" : "Not connected"}</StatusPill>
        <PillButton disabled={model.cloudBusyKey === "google-connect"} onClick={model.onGoogleConnect}>{model.cloudBusyKey === "google-connect" ? "Connecting…" : connected || model.cloudLibraries.google?.reconnect_required ? "Reconnect" : "Connect"}</PillButton>
      </MeridianCard>
      <MeridianCard>
        <div className="meridian-row-copy"><strong>Add a cloud library</strong><small>Personal libraries appear only in your Library; shared libraries appear for every user.</small></div>
        <div className="meridian-cloud-form">
          <label><span>DESTINATION</span><Segmented ariaLabel="Cloud library destination" onChange={setDestination} options={model.isAdmin ? [{ value: "personal", label: "Personal" }, { value: "shared", label: "Everyone" }] : [{ value: "personal", label: "Personal" }]} value={destination} /></label>
          <label><span>RESOURCE TYPE</span><Segmented ariaLabel="Cloud resource type" onChange={(value) => setDraft((current) => ({ ...current, resource_type: value }))} options={[{ value: "folder", label: "Folder" }, { value: "shared_drive", label: "Shared drive" }]} value={draft.resource_type} /></label>
          <label className="meridian-cloud-form__id"><span>GOOGLE DRIVE RESOURCE ID</span><span><input disabled={!connected} onChange={(event) => setDraft((current) => ({ ...current, resource_id: event.target.value }))} placeholder="Paste the folder or shared drive ID" value={draft.resource_id} /><button disabled={!connected || model.cloudBusyKey.startsWith("add-")} onClick={() => model.onAddCloudSource(destination)} type="button">Add</button></span></label>
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
          {!sources.length ? <p className="meridian-muted-copy">No personal cloud libraries added yet.</p> : null}
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
  return (
    <div className="meridian-panel-stack">
      <Segmented ariaLabel="Hidden title scope" onChange={setScope} options={model.isAdmin ? [{ value: "personal", label: `For me (${model.hiddenItems.length})` }, { value: "global", label: `For everyone (${model.globalHiddenItems.length})` }] : [{ value: "personal", label: `For me (${model.hiddenItems.length})` }]} value={scope} />
      <MeridianCard className="meridian-hidden-card" id="hidden-list">
        {model.hiddenLoading ? <p className="meridian-muted-copy">Loading hidden titles…</p> : null}
        {!model.hiddenLoading && !items.length ? <div className="meridian-empty-state"><strong>You have no hidden movies right now.</strong><span>These items stay out of your library until you restore them or change their hidden scope.</span></div> : null}
        {visible.map((item) => (
          <article className="meridian-hidden-row" key={item.id}>
            <span className="meridian-hidden-row__initial">{String(item.title || "E").trim().charAt(0).toUpperCase() || "E"}</span>
            <span className="meridian-row-copy">
              <strong>{item.title}</strong>
              {item.year || item.edition_label ? <small>{[item.year, item.edition_label].filter(Boolean).join(" · ")}</small> : null}
            </span>
            <PillButton className="meridian-pill-button--primary" onClick={() => (personal ? model.onShowAgain(item.id) : model.onShowForEveryone(item.id))}>Show again</PillButton>
            {personal && model.isAdmin ? <PillButton onClick={() => model.onHideForEveryone(item)}>Hide for everyone</PillButton> : null}
            {!personal ? <PillButton onClick={() => model.onHideForMe(item)}>Hide for me</PillButton> : null}
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

function ServerPanel({ model }) {
  const [step, setStep] = useState(0);
  const steps = ["1 · Origin", "2 · Credentials", "3 · Register", "4 · Connect"];
  const categoryRows = [
    ["Movies", model.sharedReference.category_summary?.movies || []],
    ["TV", model.sharedReference.category_summary?.tv || []],
    ["Cartoon", model.sharedReference.category_summary?.cartoon || []],
    ["Anime", model.sharedReference.category_summary?.anime || []],
  ];
  return (
    <div className="meridian-panel-stack">
      <MeridianCard id="google-drive-oauth-setup">
        <div className="meridian-card-header"><span className="meridian-row-copy"><strong>Google Drive OAuth Setup</strong><small>Configure the secure OAuth connection used by cloud libraries.</small></span><StatusPill tone={model.googleSetup.configuration_state === "ready" ? "success" : "neutral"}>{model.googleSetupBadgeLabel}</StatusPill></div>
        <div className="meridian-stepper">
          {steps.map((label, index) => <button className={index === step ? "is-active" : index < step ? "is-complete" : ""} key={label} onClick={() => setStep(index)} type="button"><i /><span>{label}</span></button>)}
        </div>
        <form className="meridian-oauth-form" onSubmit={model.onGoogleSetupSave}>
          {step === 0 ? <label><span>HTTPS APP ORIGIN</span><input onChange={(event) => model.setGoogleSetupDraft((current) => ({ ...current, https_origin: event.target.value }))} placeholder="https://elvern.example.com" value={model.googleSetupDraft.https_origin} /></label> : null}
          {step === 1 ? <><label><span>GOOGLE OAUTH CLIENT ID</span><input onChange={(event) => model.setGoogleSetupDraft((current) => ({ ...current, client_id: event.target.value }))} value={model.googleSetupDraft.client_id} /></label><label><span>GOOGLE OAUTH CLIENT SECRET</span>{model.secretInput}</label></> : null}
          {step === 2 ? <div className="meridian-register-values"><span>AUTHORIZED JAVASCRIPT ORIGIN<strong>{model.googleSetup.javascript_origin || "Set an HTTPS origin first."}</strong></span><span>AUTHORIZED REDIRECT URI<strong>{model.googleSetup.redirect_uri || "Available after the origin is configured."}</strong><PillButton disabled={!model.googleSetup.redirect_uri} onClick={model.onCopyGoogleCallback}><Copy aria-hidden="true" size={14} />Copy</PillButton></span></div> : null}
          {step === 3 ? <div className="meridian-oauth-status-grid">{[["OAuth setup", model.googleSetupBadgeLabel], ["Account health", model.googleConnectionHealth], ["Source health", model.sourceHealth], ["HTTPS origin", model.googleSetup.missing_fields?.includes("https_origin") ? "Missing" : "Configured"], ["Client ID", model.googleSetup.missing_fields?.includes("client_id") ? "Missing" : "Configured"], ["Client Secret", model.googleSetup.missing_fields?.includes("client_secret") ? "Missing" : "Configured"]].map(([key, value]) => <span key={key}><small>{key}</small><strong>{value}</strong></span>)}</div> : null}
          <div className="meridian-step-actions"><PillButton disabled={step === 0} onClick={() => setStep((current) => Math.max(0, current - 1))}>Back</PillButton>{step < 3 ? <PillButton className="meridian-pill-button--primary" onClick={() => setStep((current) => Math.min(3, current + 1))}>Continue</PillButton> : <button className="meridian-pill-button meridian-pill-button--primary" disabled={model.googleSetupSaving} type="submit">{model.googleSetupSaving ? "Saving…" : "Save Google Drive Setup"}</button>}</div>
        </form>
      </MeridianCard>

      <MeridianCard>
        <div className="meridian-card-header"><span className="meridian-row-copy"><strong>Library reference locations</strong><small>Parent folders where Elvern scans for media folders.</small></span><PillButton onClick={() => model.onOpenDirectoryPicker("shared-library")}><FolderOpen aria-hidden="true" size={14} />Browse</PillButton></div>
        <textarea className="meridian-path-input" onChange={(event) => model.setSharedReferenceInput(event.target.value)} rows="3" value={model.sharedReferenceInput} />
        <div className="meridian-path-summary"><span>Active locations<strong>{model.sharedReference.effective_value || "Unknown"}</strong></span><span>Default location<strong>{model.sharedReference.default_value || "Unknown"}</strong></span></div>
        <div className="meridian-reference-categories">
          {categoryRows.map(([label, entries]) => (
            <div key={label}>
              <strong>{label}</strong>
              <span>{entries.length ? entries.map((entry) => entry.name || entry.path).join(", ") : "None found"}</span>
            </div>
          ))}
        </div>
        <div className="meridian-path-rules">
          <span className="meridian-row-copy"><strong>Path rules</strong><small>{model.sharedReference.validation_rules?.[0] || "Choose parent folders where Elvern should look for media."}</small></span>
          <div className="meridian-chip-row" aria-label="Supported media folder suffixes">
            {["-M", "-TV", "-AN", "-C", "-L", "-S", "-X"].map((suffix) => <span className="meridian-chip" key={suffix}>{suffix}</span>)}
          </div>
          {(model.sharedReference.validation_rules || []).slice(1).map((rule) => <small className="meridian-rule-copy" key={rule}>{rule}</small>)}
        </div>
        <div className="meridian-actions"><button className="meridian-pill-button meridian-pill-button--primary" disabled={model.sharedReferenceSaving} onClick={model.onSharedReferenceSave} type="button">{model.sharedReferenceSaving ? "Saving…" : "Save reference locations"}</button></div>
      </MeridianCard>

      <MeridianCard>
        <div className="meridian-card-header"><span className="meridian-row-copy"><strong>Poster reference location</strong><small>Global poster directory used for every user.</small></span><PillButton onClick={() => model.onOpenDirectoryPicker("poster-reference")}><FolderOpen aria-hidden="true" size={14} />Browse</PillButton></div>
        <input className="meridian-path-input" onChange={(event) => model.setPosterReferenceInput(event.target.value)} value={model.posterReferenceInput} />
        <div className="meridian-path-summary"><span>Current path<strong>{model.posterReference.effective_value || "Unknown"}</strong></span><span>Default path<strong>{model.posterReference.default_value || "Unknown"}</strong></span></div>
        {(model.posterReference.validation_rules || []).length ? <div className="meridian-path-rules"><span className="meridian-row-copy"><strong>Accepted paths</strong><small>{model.posterReference.validation_rules.join(" ")}</small></span></div> : null}
        <div className="meridian-actions"><button className="meridian-pill-button meridian-pill-button--primary" disabled={model.posterReferenceSaving} onClick={model.onPosterReferenceSave} type="button">{model.posterReferenceSaving ? "Saving…" : "Save poster location"}</button></div>
      </MeridianCard>
    </div>
  );
}

export function MeridianSettingsView({ model, tab }) {
  return (
    <div className="meridian-settings-view">
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
