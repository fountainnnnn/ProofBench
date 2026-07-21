// Transport-error firewall for the live smoke test.
//
// Every request in the live smoke spec carries `Authorization: Bearer <token>`.
// When an APIRequestContext operation fails at the transport layer, Playwright
// attaches its internal call log to the thrown error — and that call log renders
// the request headers, including the bearer token. This happens regardless of
// whether traces are enabled, because the call log lives on the error itself,
// not in an artifact.
//
// So the original error can never be allowed to escape. `guarded` runs an
// operation and, on any throw, discards the original entirely and raises a fresh
// Error carrying only a caller-supplied fixed label.
//
// The discard has to be total, which drives three deliberate choices:
//   - `catch {}` binds nothing, so there is no reference to re-attach by mistake
//   - the replacement is constructed with no `cause`, so the original is not
//     reachable through the ES2022 error chain (`{ cause }` would serialize the
//     call log straight back into reporter output)
//   - the label is the whole message; nothing derived from the failure, the URL,
//     the headers, or the body is interpolated into it
//
// The replacement's own stack is captured where it is constructed, so it
// describes this file and the spec's call site only. It never contains frames or
// data from the discarded error.
export async function guarded(label, operation) {
  try {
    return await operation();
  } catch {
    // Intentionally no binding, no cause, no rethrow of the original.
    throw new Error(label);
  }
}
