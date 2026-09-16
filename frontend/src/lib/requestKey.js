/**
 * A UUID for the Idempotency-Key header.
 *
 * `crypto.randomUUID` only exists in a secure context — HTTPS or localhost. Anywhere else
 * (a LAN address during testing, a preview host on plain HTTP) it is undefined, and calling
 * it threw before the request was ever made: Analyze appeared to do nothing at all.
 *
 * `crypto.getRandomValues` has no such restriction, so the fallback is a v4 UUID built from
 * it. The last resort uses Math.random, which is not cryptographically strong — acceptable
 * here and nowhere else, because this value is never a secret. It only has to be unique
 * enough that two of the user's own requests don't collide, and the server validates the
 * shape.
 */
export function requestKey() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }

  const bytes = new Uint8Array(16)
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(bytes)
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256)
    }
  }

  bytes[6] = (bytes[6] & 0x0f) | 0x40      // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80      // variant 10xx

  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return [
    hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20),
  ].join('-')
}
