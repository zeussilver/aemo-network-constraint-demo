import type { Metadata } from "next";
import { Forbidden, PageHeader } from "@/components/ui";
import { requireActor } from "@/lib/dal";
import { db } from "@/lib/db";
import { can } from "@/lib/rbac";
import { param } from "@/lib/search-params";
import { listCustomerOptions } from "@/lib/services/customers";
import { createSiteAction } from "../actions";
import { SiteForm } from "../site-form";

export const metadata: Metadata = { title: "New site" };

export default async function NewSitePage({ searchParams }: PageProps<"/sites/new">) {
  const actor = await requireActor();
  if (!can(actor.role, "site:write")) return <Forbidden what="creating sites" />;
  const customers = await listCustomerOptions(db, actor);
  const defaultCustomerId = param(await searchParams, "customerId");
  return (
    <>
      <PageHeader title="New site" subtitle="Create the site first, then assign its AEMO DUIDs on the site page." />
      <SiteForm action={createSiteAction} customers={customers} defaultCustomerId={defaultCustomerId} cancelHref="/sites" />
    </>
  );
}
