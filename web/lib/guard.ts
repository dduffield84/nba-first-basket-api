import 'server-only';

// Same-origin check for this site's own POST routes (the session cookie is
// SameSite=Lax, and this refuses cross-site form posts as well).
export function sameOrigin(req: Request): boolean {
  const origin = req.headers.get('origin');
  if (!origin) return false;
  const host = req.headers.get('x-forwarded-host') ?? req.headers.get('host');
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}
