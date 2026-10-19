"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cx } from "./ui";

type Item = { href: string; label: string };

export function NavLinks({ items }: { items: Item[] }) {
  const pathname = usePathname();
  return (
    <ul className="flex flex-col gap-0.5">
      {items.map((item) => {
        const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <li key={item.href}>
            <Link
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={cx(
                "block rounded-md px-3 py-1.5 text-sm",
                active ? "bg-slate-800 font-medium text-white" : "text-slate-300 hover:bg-slate-800 hover:text-white",
              )}
            >
              {item.label}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
