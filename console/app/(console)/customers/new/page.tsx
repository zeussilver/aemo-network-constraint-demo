import type { Metadata } from "next";
import { Forbidden, PageHeader } from "@/components/ui";
import { requireActor } from "@/lib/dal";
import { can } from "@/lib/rbac";
import { createCustomerAction } from "../actions";
import { CustomerForm } from "../customer-form";

export const metadata: Metadata = { title: "New customer" };

export default async function NewCustomerPage() {
  const actor = await requireActor();
  if (!can(actor.role, "customer:write")) return <Forbidden what="creating customers" />;
  return (
    <>
      <PageHeader title="New customer" subtitle="Customers created here are demo records and are marked synthetic." />
      <CustomerForm action={createCustomerAction} cancelHref="/customers" />
    </>
  );
}
