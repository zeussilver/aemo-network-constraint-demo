import { NavLinks } from "@/components/nav";
import { Badge } from "@/components/ui";
import { requireActor } from "@/lib/dal";
import { can } from "@/lib/rbac";
import { signOut } from "./actions";

export default async function ConsoleLayout({ children }: LayoutProps<"/">) {
  const actor = await requireActor();
  const items = [
    { href: "/", label: "Overview" },
    { href: "/sites", label: "Sites" },
    { href: "/customers", label: "Customers" },
    { href: "/status", label: "Data status" },
    ...(can(actor.role, "audit:read") ? [{ href: "/audit", label: "Audit log" }] : []),
  ];
  return (
    <div className="flex min-h-screen">
      <aside className="flex w-56 shrink-0 flex-col justify-between bg-slate-900 px-3 py-4">
        <div>
          <p className="px-3 pb-4 text-sm font-semibold text-white">Site Ops Console</p>
          <nav aria-label="Main">
            <NavLinks items={items} />
          </nav>
        </div>
        <div className="border-t border-slate-700 px-3 pt-3 text-xs text-slate-300">
          <p className="truncate" title={actor.email}>
            {actor.email}
          </p>
          <p className="mt-1">
            <Badge tone={actor.role === "admin" ? "amber" : "blue"}>{actor.role}</Badge>
          </p>
          <form action={signOut} className="mt-3">
            <button type="submit" className="text-slate-300 underline hover:text-white">
              Sign out
            </button>
          </form>
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-6 py-6">{children}</main>
    </div>
  );
}
