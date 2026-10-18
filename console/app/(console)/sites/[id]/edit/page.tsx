import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { Forbidden, PageHeader } from "@/components/ui";
import { requireActor } from "@/lib/dal";
import { db } from "@/lib/db";
import { notFoundOnMissing } from "@/lib/page-guards";
import { can } from "@/lib/rbac";
import { listCustomerOptions } from "@/lib/services/customers";
import { getSite } from "@/lib/services/sites";
import { updateSiteAction } from "../../actions";
import { SiteForm } from "../../site-form";

export const metadata: Metadata = { title: "Edit site" };

export default async function EditSitePage({ params }: PageProps<"/sites/[id]/edit">) {
  const actor = await requireActor();
  if (!can(actor.role, "site:write")) return <Forbidden what="editing sites" />;
  const { id } = await params;
  const [site, customers] = await Promise.all([getSite(db, actor, id).catch(notFoundOnMissing), listCustomerOptions(db, actor)]);
  if (site.archivedAt) redirect(`/sites/${id}`);
  return (
    <>
      <PageHeader title={`Edit ${site.name}`} />
      <SiteForm action={updateSiteAction} site={site} customers={customers} cancelHref={`/sites/${id}`} />
    </>
  );
}
