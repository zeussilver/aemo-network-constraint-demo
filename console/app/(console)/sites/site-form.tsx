"use client";

import { useActionState } from "react";
import { fieldError, FormMessage, lastValue, SubmitButton } from "@/components/form-status";
import { Field, Input, LinkButton, Select } from "@/components/ui";
import { idle, type ActionResult } from "@/lib/action-result";

type Site = {
  id: string;
  version: number;
  name: string;
  customerId: string;
  region: string;
  fuel: string;
  registeredCapacityMw: number;
  status: string;
};

const REGIONS = ["NSW1", "QLD1", "VIC1", "SA1", "TAS1"];

export function SiteForm({
  action,
  site,
  customers,
  defaultCustomerId,
  cancelHref,
}: {
  action: (prev: ActionResult, formData: FormData) => Promise<ActionResult>;
  site?: Site;
  customers: Array<{ id: string; name: string }>;
  defaultCustomerId?: string;
  cancelHref: string;
}) {
  const [state, formAction] = useActionState(action, idle);
  const invalid = (name: string) => !!fieldError(state, name);
  return (
    <form action={formAction} className="flex max-w-xl flex-col gap-4" noValidate>
      <FormMessage state={state} />
      {site && (
        <>
          <input type="hidden" name="id" value={site.id} />
          <input type="hidden" name="version" value={site.version} />
        </>
      )}
      <Field label="Site name" name="name" error={fieldError(state, "name")}>
        <Input id="name" name="name" defaultValue={lastValue(state, "name", site?.name)} aria-invalid={invalid("name")} />
      </Field>
      <Field label="Customer" name="customerId" error={fieldError(state, "customerId")}>
        <Select id="customerId" name="customerId" defaultValue={lastValue(state, "customerId", site?.customerId ?? defaultCustomerId)} aria-invalid={invalid("customerId")}>
          <option value="">Choose a customer</option>
          {customers.map((customer) => (
            <option key={customer.id} value={customer.id}>
              {customer.name}
            </option>
          ))}
        </Select>
      </Field>
      <div className="grid grid-cols-2 gap-4">
        <Field label="NEM region" name="region" error={fieldError(state, "region")}>
          <Select id="region" name="region" defaultValue={lastValue(state, "region", site?.region ?? "NSW1")}>
            {REGIONS.map((region) => (
              <option key={region} value={region}>
                {region}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Fuel" name="fuel" error={fieldError(state, "fuel")}>
          <Select id="fuel" name="fuel" defaultValue={lastValue(state, "fuel", site?.fuel ?? "SOLAR")}>
            <option value="SOLAR">Solar</option>
            <option value="WIND">Wind</option>
          </Select>
        </Field>
        <Field label="Registered capacity (MW)" name="registeredCapacityMw" error={fieldError(state, "registeredCapacityMw")}>
          <Input
            id="registeredCapacityMw"
            name="registeredCapacityMw"
            inputMode="decimal"
            defaultValue={lastValue(state, "registeredCapacityMw", site?.registeredCapacityMw)}
            aria-invalid={invalid("registeredCapacityMw")}
          />
        </Field>
        <Field label="Status" name="status" error={fieldError(state, "status")}>
          <Select id="status" name="status" defaultValue={lastValue(state, "status", site?.status ?? "OPERATING")}>
            <option value="OPERATING">Operating</option>
            <option value="COMMISSIONING">Commissioning</option>
            <option value="DECOMMISSIONED">Decommissioned</option>
          </Select>
        </Field>
      </div>
      <div className="flex gap-2">
        <SubmitButton>{site ? "Save changes" : "Create site"}</SubmitButton>
        <LinkButton href={cancelHref}>Cancel</LinkButton>
      </div>
    </form>
  );
}
