// Shared error shape for both the real fetch layer (api.ts) and the mock
// layer (mock.ts), so mock-mode failures (e.g. approving a non-PENDING
// recommendation) look identical to real 401/409 responses to the UI.

export class ApiError extends Error {
  status?: number;
  code?: string;

  constructor(message: string, status?: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}
