import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getActor } from "@/lib/dal";
import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage() {
  if (await getActor()) redirect("/");
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <h1 className="text-lg font-semibold">Site Ops Console</h1>
        <p className="mb-5 mt-1 text-sm text-slate-600">Staff sign-in. Accounts are created by an administrator.</p>
        <LoginForm />
      </div>
    </main>
  );
}
