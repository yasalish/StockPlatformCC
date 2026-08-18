/**
 * app.js's module object, declared for TypeScript. It is a lexical `const` in a
 * classic script, not a property of window — see getBN() in MarketGrid.vue.
 */
declare const BN:
  | {
      toggleWatch(el: HTMLElement): Promise<void>;
      initTable(tableId: string, filterInputId?: string): void;
      fmt(n: number): string;
    }
  | undefined;
