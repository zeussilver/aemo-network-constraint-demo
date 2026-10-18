import { z } from "zod";

/** Empty form fields arrive as "" — treat them as absent for optional fields. */
const optionalText = (max: number) =>
  z
    .string()
    .trim()
    .max(max)
    .transform((value) => (value === "" ? null : value))
    .nullable()
    .optional();

const name = z.string().trim().min(2, "At least 2 characters").max(120, "At most 120 characters");

export const customerInput = z.object({
  name,
  contactEmail: z.string().trim().pipe(z.email("Enter a valid email address")),
  status: z.enum(["ACTIVE", "ONBOARDING", "INACTIVE"]),
  notes: optionalText(1000),
});
export type CustomerInput = z.infer<typeof customerInput>;

export const siteInput = z.object({
  name,
  customerId: z.string().trim().min(1, "Choose a customer"),
  region: z.enum(["NSW1", "QLD1", "VIC1", "SA1", "TAS1"]),
  fuel: z.enum(["SOLAR", "WIND"]),
  registeredCapacityMw: z.coerce
    .number("Enter a number")
    .positive("Must be greater than 0")
    .max(5000, "At most 5,000 MW"),
  status: z.enum(["OPERATING", "COMMISSIONING", "DECOMMISSIONED"]),
});
export type SiteInput = z.infer<typeof siteInput>;

export const duidInput = z
  .string()
  .trim()
  .toUpperCase()
  .regex(/^[A-Z0-9_]{2,20}$/, "A DUID is 2–20 letters, digits or underscores");

export const versionInput = z.coerce.number().int().min(1);
export const idInput = z.string().trim().min(1);

/** Read the named fields of a form into a plain object for zod. */
export function formFields(formData: FormData, keys: readonly string[]): Record<string, string> {
  return Object.fromEntries(keys.map((key) => [key, String(formData.get(key) ?? "")]));
}
