/**
 * API base URL for the frontend. Empty = same origin (localhost or CloudFront).
 * Set by deploy script when using S3-only (no-CloudFront) so /api/* calls go to API Gateway.
 */
window.CHESSMATCH_API_BASE = '';
