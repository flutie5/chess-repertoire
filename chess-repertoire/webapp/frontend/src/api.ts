/** API client helpers — re-exported types for gradual extraction from app.ts. */

export type ApiError = Error & {
  status?: number;
  code?: string | null;
  httpStatus?: number;
  isProxyMiss?: boolean;
};

export function isLoginRequired(err: unknown): boolean {
  const e = err as ApiError;
  return e?.code === "login_required" || e?.status === 401;
}

export function isProRequired(err: unknown): boolean {
  const e = err as ApiError;
  return e?.code === "pro_required" || e?.status === 402;
}
