"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { toActionError, type ActionResult } from "@/lib/action-result";
import { getActor } from "@/lib/dal";
import { db } from "@/lib/db";
import { assignUnit, createSite, removeUnit, setSiteArchived, updateSite } from "@/lib/services/sites";
import { duidInput, formFields, idInput, siteInput, versionInput } from "@/lib/validation";

const FIELDS = ["name", "customerId", "region", "fuel", "registeredCapacityMw", "status"] as const;
const SIGNED_OUT: ActionResult = { ok: false, error: "Your session has ended. Sign in again." };

function revalidateSite(id: string) {
  revalidatePath("/sites");
  revalidatePath(`/sites/${id}`);
  revalidatePath("/customers", "layout");
}

export async function createSiteAction(_prev: ActionResult, formData: FormData): Promise<ActionResult> {
  const actor = await getActor();
  if (!actor) return SIGNED_OUT;
  const values = formFields(formData, FIELDS);
  let id: string;
  try {
    id = (await createSite(db, actor, siteInput.parse(values))).id;
  } catch (error) {
    return toActionError(error, values);
  }
  revalidateSite(id);
  redirect(`/sites/${id}`);
}

export async function updateSiteAction(_prev: ActionResult, formData: FormData): Promise<ActionResult> {
  const actor = await getActor();
  if (!actor) return SIGNED_OUT;
  const values = formFields(formData, FIELDS);
  let id: string;
  try {
    id = idInput.parse(formData.get("id"));
    await updateSite(db, actor, id, versionInput.parse(formData.get("version")), siteInput.parse(values));
  } catch (error) {
    return toActionError(error, values);
  }
  revalidateSite(id);
  redirect(`/sites/${id}`);
}

export async function setSiteArchivedAction(_prev: ActionResult, formData: FormData): Promise<ActionResult> {
  const actor = await getActor();
  if (!actor) return SIGNED_OUT;
  try {
    const id = idInput.parse(formData.get("id"));
    const archive = formData.get("archive") === "1";
    await setSiteArchived(db, actor, id, versionInput.parse(formData.get("version")), archive);
    revalidateSite(id);
    return { ok: true, message: archive ? "Site archived." : "Site restored." };
  } catch (error) {
    return toActionError(error);
  }
}

export async function assignUnitAction(_prev: ActionResult, formData: FormData): Promise<ActionResult> {
  const actor = await getActor();
  if (!actor) return SIGNED_OUT;
  const values = { duid: String(formData.get("duid") ?? "") };
  try {
    const siteId = idInput.parse(formData.get("siteId"));
    const duid = duidInput.parse(values.duid);
    await assignUnit(db, actor, siteId, duid);
    revalidateSite(siteId);
    return { ok: true, message: `${duid} assigned.` };
  } catch (error) {
    return toActionError(error, values);
  }
}

export async function removeUnitAction(_prev: ActionResult, formData: FormData): Promise<ActionResult> {
  const actor = await getActor();
  if (!actor) return SIGNED_OUT;
  try {
    const duid = duidInput.parse(formData.get("duid"));
    const unit = await removeUnit(db, actor, duid);
    revalidateSite(unit.siteId);
    return { ok: true, message: `${duid} removed from this site.` };
  } catch (error) {
    return toActionError(error);
  }
}
