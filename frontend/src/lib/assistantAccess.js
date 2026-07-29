export function canAccessAssistant(user) {
  return Boolean(
    user
    && (
      user.role === "admin"
      || user.assistant_beta_enabled === true
    )
  );
}


export function resolveAssistantNavigationTarget(user) {
  if (!canAccessAssistant(user)) {
    return null;
  }
  return user.role === "admin" ? "/admin/assistant" : "/assistant";
}
