export const CONTROL_CENTER_BEFORE_NAVIGATION_EVENT = "elvern:control-center-before-navigation";

export function requestControlCenterNavigation(destination, proceed) {
  if (typeof window === "undefined") {
    return true;
  }
  return window.dispatchEvent(new CustomEvent(CONTROL_CENTER_BEFORE_NAVIGATION_EVENT, {
    cancelable: true,
    detail: {
      destination,
      proceed,
    },
  }));
}
