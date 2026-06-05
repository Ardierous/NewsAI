type Props = {
  message: string;
  compact?: boolean;
};

export function isProxyapiBudgetError(text: string): boolean {
  const t = text.toLowerCase();
  return (
    t.includes("402") ||
    t.includes("budget exceeded") ||
    t.includes("нулевой баланс") ||
    (t.includes("proxyapi") && (t.includes("бюджет") || t.includes("баланс")))
  );
}

export function proxyapiBudgetAlertTitle(message: string): string {
  const t = message.toLowerCase();
  if (t.includes("нулевой баланс")) {
    return "Нулевой баланс ProxyAPI";
  }
  return "Исчерпан бюджет ключа ProxyAPI";
}

export function ProxyapiBudgetAlert({ message, compact }: Props) {
  return (
    <div
      role="alert"
      className="card"
      style={{
        borderColor: "#ef4444",
        background: "rgba(239, 68, 68, 0.14)",
        color: "#fecaca",
        marginTop: compact ? 12 : undefined,
        marginBottom: compact ? 0 : undefined,
      }}
    >
      <h3 style={{ marginTop: 0, fontSize: compact ? "1rem" : "1.08rem", color: "#fca5a5" }}>
        {proxyapiBudgetAlertTitle(message)}
      </h3>
      <p style={{ margin: "0 0 12px", lineHeight: 1.55, fontSize: "0.96rem" }}>{message}</p>
      <p className="wizard-hint-do" style={{ margin: 0, color: "#fde68a", fontSize: "0.92rem" }}>
        <strong>Что делать:</strong> в{" "}
        <a href="https://proxyapi.ru" target="_blank" rel="noreferrer" style={{ color: "#fde68a" }}>
          личном кабинете ProxyAPI
        </a>{" "}
        пополните счёт или измените ограничения бюджета этого API-ключа.
      </p>
    </div>
  );
}
