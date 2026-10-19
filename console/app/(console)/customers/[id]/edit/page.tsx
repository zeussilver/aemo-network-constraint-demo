import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { Forbidden, PageHeader } from "@/components/ui";
import { requireActor } from "@/lib/dal";
import { db } from "@/lib/db";
import { notFoundOnMissing } from "@/lib/page-guards";
import { can } from "@/lib/rbac";
import { getCustomer } from "@/lib/services/customers";
import { updateCustomerAction } from "../../actions";
import { CustomerForm } from "../../customer-form";

export const metadata: Metadata = { title: "Edit customer" };

export default async function EditCustomerPage({ params }: PageProps<"/customers/[id]/edit">) {
  const actor = await requireActor();
  if (!can(actor.role, "customer:write")) return <Forbidden what="editing customers" />;
  const { id } = await params;
  const customer = await getCustomer(db, actor, id).catch(notFoundOnMissing);
  if (customer.archivedAt) redirect(`/customers/${id}`);
  return (
    <>
      <PageHeader title={`Edit ${customer.name}`} />
      <CustomerForm action={updateCustomerAction} customer={customer} cancelHref={`/customers/${id}`} />
    </>
  );
}
