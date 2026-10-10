import "server-only";
import { headers } from "next/headers";

// Container Apps authentication passes the signed-in user as a base64 JSON header and strips
// any copy sent by the browser. Without it (misconfigured or local), the user has no roles.
type Claim = { typ: string; val: string };
type ClientPrincipal = { claims?: Claim[]; role_typ?: string };

const ROLE_TYPES = ["roles", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"];

export async function currentRoles(): Promise<string[]> {
  const h = await headers();
  if (process.env.CONSOLE_AUTH_MODE === "mock") {
    // Playwright only (mocked ops-api): roles come from a test header
    return (h.get("x-mock-roles") ?? process.env.CONSOLE_MOCK_ROLES ?? "viewer").split(",");
  }
  const raw = h.get("x-ms-client-principal");
  if (!raw) return [];
  try {
    const p = JSON.parse(Buffer.from(raw, "base64").toString("utf8")) as ClientPrincipal;
    const types = new Set([...ROLE_TYPES, p.role_typ ?? "roles"]);
    return (p.claims ?? []).filter((c) => types.has(c.typ)).map((c) => c.val);
  } catch {
    return [];
  }
}
