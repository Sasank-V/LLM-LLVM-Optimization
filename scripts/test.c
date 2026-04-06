#include <stdio.h>
#include <stdlib.h>
#include <math.h>

#define N 256
#define M 128
#define K 64

/* Unused globals (DCE / globalopt targets) */
double UNUSED_GLOBAL1[N][N];
int UNUSED_GLOBAL2[M];
static int UNUSED_FLAG = 1;

/* Constants for SCCP */
const int CONST_TRUE = 1;
const int CONST_FALSE = 0;
const int CONST_LIMIT = 10;

/* Small inlineable helpers */
static inline double add_bias(double x) {
    return x + 1.234; /* constant add */
}

static inline double clamp_pos(double x) {
    if (x < 0.0) return 0.0;
    return x;
}

/* Dead function: never referenced */
static void debug_dump_matrix(double A[N][N]) {
    for (int i = 0; i < N; i++) {
        for (int j = 0; j < N; j++) {
            printf("%f ", A[i][j]);
        }
        printf("\n");
    }
}

/* Another dead helper */
static int expensive_unused(int x) {
    int acc = 0;
    for (int i = 0; i < 1000; i++) {
        acc += (x * i) % 7;
    }
    return acc;
}

/* Partially-dead branches: SCCP + DCE should simplify/remove */
static int mode_select(int mode) {
    int result = 0;

    if (CONST_FALSE) {        /* always false */
        result += 100;
    }

    if (CONST_TRUE) {         /* always true */
        result += 1;
    }

    /* Some real dependence on input */
    if (mode == 1) {
        result += 10;
    } else if (mode == 2) {
        result += 20;
    } else {
        result += 5;
    }

    return result;
}

/* Core compute kernel: GEMM-like + nonlinear ops */
void compute_kernel(double A[N][K], double B[K][M], double C[N][M]) {
    for (int i = 0; i < N; i++) {
        for (int j = 0; j < M; j++) {
            double sum = 0.0;

            /* Inner product */
            for (int k = 0; k < K; k++) {
                double v = A[i][k] * B[k][j];

                /* Redundant computations that instcombine can simplify */
                if (k % 2 == 0) {
                    v = v + 0.0;        /* noop */
                    v = v * 1.0;        /* noop */
                }

                sum += v;
            }

            /* Non-linear post-processing */
            sum = tanh(sum);
            sum = add_bias(sum);
            sum = clamp_pos(sum);

            C[i][j] = sum;
        }
    }
}

/* Another kernel with different pattern: many dead paths */
void stats_kernel(double X[N], double *mean_out, double *var_out) {
    double sum = 0.0;
    double sum_sq = 0.0;

    for (int i = 0; i < N; i++) {
        double v = X[i];

        /* Dead branch: never taken because of CONST_FALSE */
        if (CONST_FALSE && v > 1000.0) {
            v = v * 2.0;
        }

        /* Partially dead: CONST_TRUE simplifies condition structure */
        if (CONST_TRUE && v < -1000.0) {
            v = -1000.0;
        }

        sum += v;
        sum_sq += v * v;
    }

    double mean = sum / (double)N;
    double var = (sum_sq / (double)N) - mean * mean;

    *mean_out = mean;
    *var_out = var;
}

/* Many small wrappers to encourage inlining + cleanup */
static double wrapper1(double x) { return add_bias(x); }
static double wrapper2(double x) { return clamp_pos(wrapper1(x)); }
static double wrapper3(double x) { return wrapper2(x) * 0.5; }
static double wrapper4(double x) { return wrapper3(x) + 2.0; }

/* Used so inlining changes size */
double apply_wrappers(double *arr, int n) {
    double acc = 0.0;
    for (int i = 0; i < n; i++) {
        acc += wrapper4(arr[i]);
    }
    return acc;
}

/* Large function with unrolled style and dead local vars */
double mixed_control_flow(double *a, double *b, int n, int mode) {
    double acc1 = 0.0;
    double acc2 = 0.0;
    double dead1 = 0.0;
    double dead2 = 0.0;

    int sel = mode_select(mode);

    for (int i = 0; i < n; i++) {
        double va = a[i];
        double vb = b[i];

        if (sel > 10) {
            acc1 += va * vb;
            acc2 += va + vb;
        } else {
            acc1 += va - vb;
            acc2 += va * 2.0;
        }

        /* Dead accumulators: never used */
        dead1 += va * 0.0;
        dead2 += vb * 0.0;
    }

    /* Some final computation to keep acc1/acc2 alive */
    return acc1 + 0.1 * acc2;
}

int main(void) {
    static double A[N][K];
    static double B[K][M];
    static double C[N][M];
    static double X[N];
    double mean, var;

    /* Initialize data */
    for (int i = 0; i < N; i++) {
        X[i] = (double)(i % 17) - 8.0;
        for (int k = 0; k < K; k++) {
            A[i][k] = (double)((i + k) % 13) * 0.1;
        }
    }
    for (int k = 0; k < K; k++) {
        for (int j = 0; j < M; j++) {
            B[k][j] = (double)((k * j) % 7) * 0.2;
        }
    }

    compute_kernel(A, B, C);
    stats_kernel(X, &mean, &var);

    double extra = apply_wrappers(X, N);
    double mix = mixed_control_flow(X, X, N, 2);

    /* Print a small checksum to keep work alive */
    printf("C[0][0]=%f, mean=%f, var=%f, extra=%f, mix=%f\n",
           C[0][0], mean, var, extra, mix);

    return 0;
}
