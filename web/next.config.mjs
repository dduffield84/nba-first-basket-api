// Security headers for every page. The browser talks to this site and, for
// sign-in only, to the Edge API.
const edge = (process.env.NEXT_PUBLIC_EDGE_API_URL || '').replace(/\/+$/, '');
const dev = process.env.NODE_ENV !== 'production';

const csp = [
  "default-src 'self'",
  // Next.js needs inline scripts for hydration; eval only in development.
  "script-src 'self' 'unsafe-inline'" + (dev ? " 'unsafe-eval'" : ''),
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'" + (edge ? ' ' + edge : ''),
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join('; ');

/** @type {import('next').NextConfig} */
export default {
  poweredByHeader: false,
  async headers() {
    return [{
      source: '/:path*',
      headers: [
        { key: 'Content-Security-Policy', value: csp },
        { key: 'X-Content-Type-Options', value: 'nosniff' },
        { key: 'X-Frame-Options', value: 'DENY' },
        { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
        { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
      ],
    }, {
      source: '/(dashboard|api)/:path*',
      headers: [{ key: 'Cache-Control', value: 'no-store' }],
    }];
  },
};
