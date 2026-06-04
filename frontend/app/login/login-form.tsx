"use client";

import { useEffect, useMemo, useState } from "react";

function safeNextPath(): string {
  const params = new URLSearchParams(window.location.search);
  const candidate = params.get("next") ?? "/";
  if (!candidate.startsWith("/") || candidate.startsWith("//")) {
    return "/";
  }
  if (candidate.startsWith("/login")) {
    return "/";
  }
  return candidate;
}

export function LoginForm() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const canSubmit = useMemo(
    () => username.trim().length > 0 && password.length > 0 && !submitting,
    [password, submitting, username],
  );

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has("next")) {
      setNotice("운영자 세션이 만료되었거나 로그인이 필요합니다. 다시 로그인하세요.");
    }
  }, []);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const response = await fetch("/api/operator/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
        cache: "no-store",
      });
      if (!response.ok) {
        if (response.status === 429) {
          setError("로그인 시도가 제한되었습니다. 잠시 후 다시 시도하세요.");
        } else if (response.status === 503) {
          setError(
            "운영자 인증 설정이 제품화 기준을 통과하지 못했습니다. OPERATOR_UI_PASSWORD와 FRONTEND_AUTH_SECRET을 확인하세요.",
          );
        } else {
          setError("운영자 계정 정보를 확인하세요.");
        }
        return;
      }
      window.location.assign(safeNextPath());
    } catch {
      setError("로그인 요청에 실패했습니다. 네트워크와 프런트 프록시 상태를 확인하세요.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="w-full max-w-sm rounded border border-slate-200 bg-white p-6 shadow-sm">
      <div className="space-y-1">
        <p className="text-sm font-semibold text-slate-500">Trading MVP</p>
        <h1 className="text-2xl font-semibold text-slate-950">운영자 로그인</h1>
      </div>
      {notice ? (
        <p className="mt-4 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm font-medium text-amber-900">
          {notice}
        </p>
      ) : null}
      <div className="mt-6 space-y-4">
        <label className="block">
          <span className="text-sm font-medium text-slate-700">아이디</span>
          <input
            autoComplete="username"
            className="mt-1 w-full rounded border border-slate-300 px-3 py-2 text-slate-950 outline-none transition focus:border-slate-950"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-slate-700">비밀번호</span>
          <input
            autoComplete="current-password"
            className="mt-1 w-full rounded border border-slate-300 px-3 py-2 text-slate-950 outline-none transition focus:border-slate-950"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
      </div>
      {error ? (
        <p className="mt-4 text-sm font-medium text-red-700" role="alert">
          {error}
        </p>
      ) : null}
      <button
        type="submit"
        disabled={!canSubmit}
        className="mt-6 w-full rounded bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
      >
        {submitting ? "확인 중" : "로그인"}
      </button>
    </form>
  );
}
