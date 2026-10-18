"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { toActionError, type ActionResult } from "@/lib/action-result";
import { getActor } from "@/lib/dal";
import { db } from "@/lib/db";
import { createCustomer, setCustomerArchived, updateCustomer } from "@/lib/services/customers";
import { customerInput, formFields, idInput, versionInput } from "@/lib/validation";

const FIELDS = ["name", "contactEmail", "status", "notes"] as const;
const SIGNED_OUT: ActionResult = { ok: false, error: "Your session has ended. Sign in again." };

export async function createCustomerAction(_prev: ActionResult, formData: FormData): Promise<ActionResult> {
  const actor = await getActor();
  if (!actor) return SIGNED_OUT;
  const values = formFields(formData, FIELDS);
  let id: string;
  try {
    const customer = await createCustomer(db, actor, customerInput.parse(values));
    id = customer.id;
  } catch (error) {
    return toActionError(error, values);
  }
  revalidatePath("/customers");
  redirect(`/customers/${id}`);
}

export async function updateCustomerAction(_prev: ActionResult, formData: FormData): Promise<ActionResult> {
  const actor = await getActor();
  if (!actor) return SIGNED_OUT;
  const values = formFields(formData, FIELDS);
  let id: string;
  try {
    id = idInput.parse(formData.get("id"));
    await updateCustomer(db, actor, id, versionInput.parse(formData.get("version")), customerInput.parse(values));
  } catch (error) {
    return toActionError(error, values);
  }
  revalidatePath("/customers");
  redirect(`/customers/${id}`);
}

export async function setCustomerArchivedAction(_prev: ActionResult, formData: FormData): Promise<ActionResult> {
  const actor = await getActor();
  if (!actor) return SIGNED_OUT;
  try {
    const id = idInput.parse(formData.get("id"));
    const archive = formData.get("archive") === "1";
    await setCustomerArchived(db, actor, id, versionInput.parse(formData.get("version")), archive);
    revalidatePath("/customers");
    revalidatePath(`/customers/${id}`);
    return { ok: true, message: archive ? "Customer archived." : "Customer restored." };
  } catch (error) {
    return toActionError(error);
  }
}
