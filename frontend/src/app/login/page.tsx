"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, LockKeyhole, ShieldCheck } from "lucide-react";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "登录失败");
      const next = new URLSearchParams(window.location.search).get("next");
      router.replace(next || "/");
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登录失败");
    } finally {
      setBusy(false);
    }
  }

  return <main className="grid min-h-[100dvh] place-items-center bg-[#07110b] px-5 text-white">
    <section className="w-full max-w-md rounded-3xl border border-white/10 bg-white/[.06] p-8 shadow-2xl backdrop-blur">
      <div className="flex items-center gap-3"><div className="grid h-12 w-12 place-items-center rounded-xl bg-white text-xl font-black text-[#172019]">D.</div><div><p className="text-[10px] font-semibold tracking-[.24em] text-[#85c82b]">RISK INTELLIGENCE</p><h1 className="text-xl font-semibold">D.Risk 内部工作台</h1></div></div>
      <div className="mt-8 flex h-12 w-12 items-center justify-center rounded-2xl bg-[#85c82b] text-[#102006]"><LockKeyhole /></div>
      <h2 className="mt-5 text-2xl font-semibold">登录投后监测系统</h2>
      <p className="mt-2 text-sm leading-6 text-white/50">财务材料、规则判断和审批记录仅对内部账号开放。</p>
      <form onSubmit={submit} className="mt-7 space-y-4">
        <label className="block text-sm"><span className="mb-2 block text-white/70">账号</span><input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" className="w-full rounded-xl border border-white/10 bg-white px-4 py-3 text-[#172019] outline-none focus:ring-2 focus:ring-[#85c82b]" /></label>
        <label className="block text-sm"><span className="mb-2 block text-white/70">密码</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" className="w-full rounded-xl border border-white/10 bg-white px-4 py-3 text-[#172019] outline-none focus:ring-2 focus:ring-[#85c82b]" /></label>
        {error && <p className="rounded-xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</p>}
        <button disabled={busy || !username || !password} className="flex w-full items-center justify-center gap-2 rounded-xl bg-[#78be20] px-4 py-3 font-semibold text-[#102006] disabled:opacity-50">{busy ? <Loader2 size={17} className="animate-spin" /> : <ShieldCheck size={17} />}{busy ? "正在验证" : "登录"}</button>
      </form>
    </section>
  </main>;
}
