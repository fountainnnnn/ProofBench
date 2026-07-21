export function acquireOperation(ref) {
  if (ref.current) return false;
  ref.current = true;
  return true;
}

export function releaseOperation(ref) {
  ref.current = false;
}
