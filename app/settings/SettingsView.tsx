"use client";

/**
 * Keys, model choice, embeddings, and what this deployment can actually do.
 *
 * The keys section says plainly where a key goes and what it is spent on,
 * because the honest answer is unusual: it is kept in this browser, sent as a
 * header on your own requests, held for the lifetime of one request on the
 * server and then forgotten. A page that takes an API key and says nothing
 * about where it goes has not earned it.
 */

import { useCallback, useEffect, useState } from "react";

import { useConfig } from "@/components/ConfigProvider";
import {
  Badge,
  Button,
  Card,
  ErrorNotice,
  Notice,
  SectionTitle,
  Select,
  Spinner,
  TextInput,
  cx,
} from "@/components/ui";
import { verifyProvider } from "@/lib/api";
import {
  clearAllCredentials,
  loadVault,
  setCredential,
  type KeyVault,
} from "@/lib/keys";

function ProviderRow({
  provider,
  credential,
  onSave,
}: {
  provider: {
    id: string;
    label: string;
    available: boolean;
    reason: string;
    docs_url: string;
    key_names: string[];
    key_source: string;
    needs_account: boolean;
    local: boolean;
    default_model: string;
  };
  credential: { key?: string; base?: string; account?: string };
  onSave: (id: string, next: { key?: string; base?: string; account?: string }) => void;
}) {
  const [key, setKey] = useState(credential.key ?? "");
  const [account, setAccount] = useState(credential.account ?? "");
  const [base, setBase] = useState(credential.base ?? "");
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  async function check() {
    onSave(provider.id, { key, account, base });
    setChecking(true);
    setResult(null);
    try {
      const response = await verifyProvider(provider.id);
      setResult(
        response.ok
          ? { ok: true, text: `Works. ${response.models?.length ?? 0} models callable.` }
          : { ok: false, text: `${response.message ?? "Rejected."} ${response.hint ?? ""}` },
      );
    } catch (exception) {
      setResult({
        ok: false,
        text: exception instanceof Error ? exception.message : String(exception),
      });
    } finally {
      setChecking(false);
    }
  }

  return (
    <Card className="p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-ink">{provider.label}</span>
        {provider.available ? (
          <Badge tone="good">
            {provider.key_source === "client"
              ? "your key"
              : provider.key_source === "server"
                ? "server key"
                : "ready"}
          </Badge>
        ) : (
          <Badge tone="neutral">not configured</Badge>
        )}
        {provider.local ? <Badge>runs locally</Badge> : null}
        {provider.docs_url ? (
          <a
            href={provider.docs_url}
            target="_blank"
            rel="noreferrer noopener"
            className="ml-auto text-[11px] text-accent hover:underline"
          >
            Get a key
          </a>
        ) : null}
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        {!provider.local ? (
          <TextInput
            type="password"
            value={key}
            onChange={setKey}
            placeholder={provider.key_names[0] ?? "API key"}
            label="Key"
          />
        ) : (
          <TextInput
            value={base}
            onChange={setBase}
            placeholder="http://localhost:11434"
            label="Base URL"
          />
        )}
        {provider.needs_account ? (
          <TextInput
            value={account}
            onChange={setAccount}
            placeholder="Account id"
            label="Account id"
          />
        ) : null}
      </div>

      <div className="mt-2 flex items-center gap-2">
        <Button size="sm" onClick={check} disabled={checking}>
          {checking ? "Checking..." : "Save and check"}
        </Button>
        {credential.key || credential.base ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setKey("");
              setAccount("");
              setBase("");
              onSave(provider.id, {});
            }}
          >
            Remove
          </Button>
        ) : null}
        {result ? (
          <span className={cx("text-xs", result.ok ? "text-good" : "text-danger")}>
            {result.text}
          </span>
        ) : null}
      </div>

      {!provider.available && provider.reason ? (
        <p className="mt-1.5 text-[11px] text-ink-faint">{provider.reason}</p>
      ) : null}
    </Card>
  );
}

export function SettingsView() {
  const { config, loading, error, reload, prefs, setPrefs } = useConfig();
  const [vault, setVault] = useState<KeyVault>({});

  useEffect(() => setVault(loadVault()), []);

  const save = useCallback(
    (id: string, next: { key?: string; base?: string; account?: string }) => {
      setVault(setCredential(id, next));
      // The server decides availability from the key the request carries, so
      // the config has to be refetched for the UI to agree with it.
      window.setTimeout(reload, 150);
    },
    [reload],
  );

  if (loading) {
    return (
      <div className="py-20 text-center">
        <Spinner label="Loading settings" />
      </div>
    );
  }
  if (error || !config) {
    return (
      <div className="py-8">
        <ErrorNotice error={error || "The backend did not respond."} />
      </div>
    );
  }

  const chosenProvider = config.providers.find((p) => p.id === prefs.provider);

  return (
    <div className="py-5">
      <h1 className="text-lg font-semibold text-ink">Settings</h1>

      <div className="mt-5 grid gap-6 lg:grid-cols-[1fr_360px]">
        <div className="space-y-6">
          <section>
            <SectionTitle>Model providers</SectionTitle>
            <Notice tone="neutral" title="Where a key you paste here goes">
              It is stored in this browser only, and attached as a header to the
              requests you make. The server holds it for the lifetime of one
              request and forgets it: it is never written to disk, never logged
              and never sent anywhere except the provider you pasted it for.
            </Notice>
            <div className="mt-3 space-y-2">
              {config.providers.map((provider) => (
                <ProviderRow
                  key={provider.id}
                  provider={provider}
                  credential={vault[provider.id] ?? {}}
                  onSave={save}
                />
              ))}
            </div>
            <Button
              variant="danger"
              size="sm"
              className="mt-3"
              onClick={() => {
                if (!window.confirm("Remove every key stored in this browser?")) return;
                clearAllCredentials();
                setVault({});
                reload();
              }}
            >
              Remove every stored key
            </Button>
          </section>

          <section>
            <SectionTitle>Embeddings</SectionTitle>
            <Card className="p-3">
              <Notice tone="neutral">
                A hosted embedding backend is never selected automatically, even
                when its key is present, because embedding a whole paper is the
                one operation that can quietly cost money. The bundled local
                encoder is the default and needs no key.
              </Notice>
              <div className="mt-3">
                <Select
                  label="Backend"
                  value={prefs.embedder ?? ""}
                  onChange={(value) => setPrefs({ embedder: value })}
                  options={[
                    { value: "", label: "Automatic (prefers the local encoder)" },
                    ...config.embedders.map((embedder) => ({
                      value: embedder.id,
                      label: `${embedder.label}${embedder.local ? " (local)" : " (hosted)"}${
                        embedder.available ? "" : " - unavailable"
                      }`,
                      disabled: !embedder.available,
                    })),
                  ]}
                />
              </div>
              <ul className="mt-3 space-y-1 text-[11px] text-ink-faint">
                {config.embedders.map((embedder) => (
                  <li key={embedder.id} className="flex justify-between gap-2">
                    <span>{embedder.label}</span>
                    <span className={embedder.available ? "text-good" : ""}>
                      {embedder.available ? `${embedder.dim}d` : embedder.reason.slice(0, 60)}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-[11px] text-ink-faint">
                Changing this means re-indexing every paper: vectors from two
                backends live in different spaces and cannot be compared.
              </p>
            </Card>
          </section>
        </div>

        <aside className="space-y-5 lg:sticky lg:top-16 lg:self-start">
          <Card className="p-3">
            <SectionTitle>Answering</SectionTitle>
            <div className="space-y-2">
              <Select
                label="Provider"
                value={prefs.provider ?? ""}
                onChange={(value) => setPrefs({ provider: value, model: "" })}
                options={[
                  { value: "", label: "Automatic" },
                  ...config.providers.map((provider) => ({
                    value: provider.id,
                    label: provider.label + (provider.available ? "" : " - no key"),
                    disabled: !provider.available,
                  })),
                ]}
              />
              {chosenProvider?.models.length ? (
                <Select
                  label="Model"
                  value={prefs.model ?? ""}
                  onChange={(value) => setPrefs({ model: value })}
                  options={[
                    { value: "", label: `Default (${chosenProvider.default_model})` },
                    ...chosenProvider.models.map((model) => ({
                      value: model.id,
                      label: model.label + (model.cheap ? " · cheap" : ""),
                    })),
                  ]}
                />
              ) : null}
            </div>
          </Card>

          <Card className="p-3">
            <SectionTitle>What this deployment can do</SectionTitle>
            <p className="mb-2 text-[11px] text-ink-faint">
              {config.runtime.tier} tier on {config.runtime.platform}, Python{" "}
              {config.runtime.python_version}
            </p>
            <ul className="space-y-1.5">
              {config.runtime.capabilities.map((capability) => (
                <li key={capability.id} className="text-[12px]">
                  <span className="flex items-center gap-1.5">
                    <span
                      className={cx(
                        "h-1.5 w-1.5 rounded-full",
                        capability.available ? "bg-good" : "bg-line-strong",
                      )}
                    />
                    <span className={capability.available ? "text-ink" : "text-ink-faint"}>
                      {capability.label}
                    </span>
                  </span>
                  {!capability.available && capability.reason ? (
                    <span className="mt-0.5 block pl-3 text-[11px] leading-snug text-ink-faint">
                      {capability.reason}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          </Card>

          <Card className="p-3">
            <SectionTitle>Access</SectionTitle>
            <TextInput
              type="password"
              label="Instance password"
              value={prefs.appPassword ?? ""}
              onChange={(value) => setPrefs({ appPassword: value })}
              placeholder="Only if this instance is protected"
            />
            <p className="mt-1.5 text-[11px] text-ink-faint">
              Sent as a header with every request. Set APP_PASSWORD on the server
              to require one.
            </p>
          </Card>
        </aside>
      </div>
    </div>
  );
}
