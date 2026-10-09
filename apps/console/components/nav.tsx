import Link from "next/link";

const links = [
  { href: "/incidents", label: "Incidents" },
  { href: "/approvals", label: "Approvals" },
  { href: "/router", label: "Router" },
  { href: "/chat", label: "Chat" },
  { href: "/demo", label: "Demo" },
] as const;

export function Nav() {
  return (
    <nav aria-label="Main" className="flex items-center gap-6 border-b px-6 py-3">
      <Link href="/incidents" className="font-semibold">
        Driftline
      </Link>
      <ul className="flex gap-4 text-sm text-muted-foreground">
        {links.map((l) => (
          <li key={l.href}>
            <Link href={l.href} className="hover:text-foreground">
              {l.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
