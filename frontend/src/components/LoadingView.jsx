export function LoadingView({ label = "Loading..." }) {
  return (
    <div className="centered-state">
      <div className="spinner" aria-hidden="true" />
      <p>{label}</p>
    </div>
  );
}
