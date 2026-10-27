/** The English dictionary must mirror the zh one exactly: same keys, string leaves stay strings,
 * function leaves keep their parameter + return types. */
export type Shape<T> = {
  [K in keyof T]: T[K] extends string
    ? string
    : T[K] extends (...a: infer A) => infer R
      ? (...a: A) => R
      : Shape<T[K]>
}
